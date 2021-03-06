#!/usr/bin/python

import gc
import gzip
import os
import sqlite3
import sys
import re
import threading
import time
import Queue
from datetime import datetime
from getopt import getopt
from htmlentitydefs import name2codepoint
from StringIO import StringIO

try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        print "Couldn't load simplejson! Using HTML interface."
        use_json = False

import requests

try:
    import whoosh
    import whoosh.fields
    import whoosh.index
except ImportError:
    whoosh = None


# ``Constants''

db_name  = None
base_url = 'dis.4chan.org'
port     = 80
board    = '/prog/'
charset  = 'utf-8'

use_json = True
verify_trips = True
no_aborn = False

progress_bar = True
threads = -1

dry_run = False


# Parse command line arguments

if '--help' in sys.argv or '-h' in sys.argv:
    print "\033[1mUSAGE\033[0m"
    print
    print "\t%s [ \033[4mOPTIONS\033[0m... ] [ \033[4mDB\033[0m ]" % sys.argv[0]
    print
    print "\033[1mOPTIONS\033[0m"
    print
    print "\t\033[1m--json\033[0m"
    print "\t\tUse the JSON interface, if possible.", "(default)" if use_json else ""
    print
    print "\t\033[1m--html\033[0m"
    print "\t\033[1m--no-json\033[0m"
    print "\t\tUse the HTML interface.", "(default)" if not use_json else ""
    print
    print "\t\033[1m--verify-trips\033[0m"
    print "\t\033[1m--no-verify-trips\033[0m"
    print "\t\tWhen using JSON, whether or not to verify ambiguous tripcodes "
    print "\t\tthrough the HTML interface. (default: %s)" % ("no", "yes")[verify_trips]
    print
    print "\t\033[1m--aborn\033[0m"
    print "\t\033[1m--no-aborn\033[0m"
    print "\t\tWhen using JSON, whether or not to include deleted posts."
    print "\t\t(default: %s)" % ("yes", "no")[no_aborn]
    print
    print "\t\033[1m--no-html\033[0m"
    print "\t\tEquivalent to \033[1m--json --no-verify-trips\033[0m."
    print
    print "\t\033[1m--progress-bar\033[0m"
    print "\t\033[1m--no-progress-bar\033[0m"
    print "\t\tWhether to display an animated progress bar or the traditional "
    print "\t\tprogress report. (default: %s)" % ("no", "yes")[progress_bar]
    print
    print "\t\033[1m--base-url\033[0m \033[4murl\033[0m"
    print "\t\tSpecify base URL. (default: \033[7m%s\033[0m)" % base_url
    print
    print "\t\033[1m--port\033[0m \033[4mport\033[0m"
    print "\t\tSpecify the port the webserver is running on. (default: %d)" % port
    print
    print "\t\033[1m--board\033[0m \033[4mboard\033[0m"
    print "\t\tSpecify board to scrape. (default: \033[7m%s\033[0m)" % board
    print
    print "\t\033[1m--charset\033[0m \033[4mcharset\033[0m"
    print "\t\tSpecify the character encoding the board uses. (default: \033[7m%s\033[0m)" % charset
    print
    print "\t\033[1m--partial\033[0m"
    print "\t\tRead a list of thread IDs on standard input and only scrape"
    print "\t\tthose (provided they're valid IDs and need updating)."
    print
    print "\t\033[1m--threads\033[0m"
    print "\t\tHow many scraper threads to use. (default: %s)" % ('auto' if threads == -1 else str(threads))
    print
    print "\t\033[1m--dry-run\033[0m"
    print "\t\033[1m--no-dry-run\033[0m"
    print "\t\tJust figure out how many threads would have to be retrieved,"
    print "\t\tdon't actually retrieve them. (default: %s)" % ("no", "yes")[dry_run]
    print
    print "\t\033[1m--index\033[0m \033[4mdir\033[0m"
    print "\t\tAlso index new posts in given directory. Omit not to index."
    print
    print "\t\033[1m--help\033[0m"
    print "\t\033[1m-h\033[0m"
    print "\t\tdisplay this message and exit"
    print

    sys.exit(0)

try:
    optlist, args = getopt(sys.argv[1:], 'h', ['json', 'html', 'no-html', 'no-json',
                                               'verify-trips', 'no-verify-trips',
                                               'progress-bar', 'no-progress-bar',
                                               'base-url=', 'port=', 'board=',
                                               'partial', 'aborn', 'no-aborn',
                                               'dry-run', 'no-dry-run',
                                               'charset=', 'threads=', 'index=',
                                               'help'])
except:
    print "Invalid argument! Use \033[1m--help\033[0m for help."
    sys.exit(1)

partial = False
idir = None

for (opt, arg) in optlist:
    if opt == '--json':
        use_json = True
    elif opt in ('--html', '--no-json'):
        use_json = False
    elif opt == '--no-html':
        use_json = True
        verify_trips = False
    elif opt == '--verify-trips':
        verify_trips = True
    elif opt == '--no-verify-trips':
        verify_trips = False
    elif opt == '--base-url':
        if arg[:7] == "http://":
            arg = arg[7:]
        base_url = arg
    elif opt == '--port':
        port = int(arg)
    elif opt == '--board':
        board = arg
    elif opt == '--progress-bar':
        progress_bar = True
    elif opt == '--no-progress-bar':
        progress_bar = False
    elif opt == '--partial':
        partial = True
        if sys.stdin.isatty():
            print "Feed me thread IDs on stdin."
            sys.exit(1)
    elif opt == '--aborn':
        no_aborn = False
    elif opt == '--no-aborn':
        no_aborn = True
    elif opt == '--charset':
        try:
            "abc".decode(arg)
        except LookupError:
            print "Unknown encoding specified: \033[1m%s\033[0m" % arg
        else:
            charset = arg
    elif opt == '--threads':
        if arg == 'auto':
            threads = -1
        else:
            try:
                threads = int(arg)
                if threads < 1:
                    threads = 1
            except ValueError:
                print "Not a number: \033[1m%s\033[0m" % arg
    elif opt == '--dry-run':
        dry_run = True
    elif opt == '--no-dry-run':
        dry_run = False
    elif opt == '--index':
        if whoosh is None:
            print "Can't index posts without Whoosh!"
            sys.exit(1)
        else:
            idir = arg

if len(board) > 0:
    if board[-1] != '/':
        board += '/'
    if board[0] != '/':
        board = '/' + board

if len(args) > 0:
    db_name = args[0]
elif not db_name:
    db_name = board[1:-1] + ".db"

if len(base_url) > 0 and base_url[-1] == '/':
    base_url = base_url[:-1]

prog_url = board
read_url = "/read" + board
json_url = "/json" + board


# Set up the database connection first

db_conn = sqlite3.connect(db_name)
db = db_conn.cursor()

try:
    db.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            thread INTEGER PRIMARY KEY,
            title TEXT,
            last_post INTEGER
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            thread INTEGER REFERENCES threads(thread),
            id INTEGER,
            author TEXT,
            email TEXT,
            trip TEXT,
            time INTEGER,
            body TEXT,
            PRIMARY KEY (thread, id)
        )""")
    db_conn.commit()
    
except sqlite3.DatabaseError:
    # Specified DB file exists, but isn't an SQLite DB file.
    print "Use a different filename for your DB."
    raise


# Open the index if we're doing that

if idir is not None:
    if not os.path.exists(idir):
        os.mkdir(idir)

    indexname = os.path.basename(db_name)

    if not whoosh.index.exists_in(idir, indexname):
        schema = whoosh.fields.Schema(thread=whoosh.fields.STORED,
                                      post=whoosh.fields.STORED,
                                      author=whoosh.fields.STORED,
                                      trip=whoosh.fields.STORED,
                                      email=whoosh.fields.STORED,
                                      time=whoosh.fields.DATETIME(stored=True),
                                      body=whoosh.fields.TEXT(stored=True))
        ix = whoosh.index.create_in(idir, schema, indexname=indexname)
        print "Created new index in \033[1m%s\033[0m." % idir
    else:
        ix = whoosh.index.open_dir(idir, indexname=indexname)

    # Need to scrub HTML cruft from post bodies and usernames
    def scrub(s, regices=[re.compile('<.*?>'),
                          re.compile(r'&#(\d+);'),
                          re.compile('&(%s);' % '|'.join(name2codepoint))]):
        if s is None:
            return u''
        s = s.replace('<br/>', '\n')
        s = s.replace("<span class='quote'>", '> ')
        s = regices[0].sub('', s)
        s = regices[1].sub(lambda m: unichr(int(m.group(1))) \
                                     if int(m.group(1)) <= 0x10ffff \
                                     else m.group(0), s)
        s = regices[2].sub(lambda m: unichr(name2codepoint[m.group(1)]), s)
        return s


# Try to fetch subject.txt

gc.set_threshold(20, 4, 2)
session = requests.session()

print "Fetching subject.txt...",
sys.stdout.flush()

def urlopen(url):
    return session.get("http://" + base_url + url, headers = {'User-Agent' : 'progscrape/1.4'}).content

try:
    subjecttxt = urlopen(prog_url + 'subject.txt')
except:
    print "Can't find it! Exiting."
    raise

print "Got it."


# Parse each line, check with DB, keep a list of all threads to be updated

regex = re.compile(u"""
    ^(?P<subject>.*)    # Subject
    <>
    .*?                 # Creator's name
    <>
    .*?                 # Thread icon
    <>
    (?P<id>-?\d*)       # Time posted/thread ID
    <>
    (?P<replies>\d*)    # Number of replies
    <>
    .*?                 # ???
    <>
    (?P<last_post>\d*)  # Time of last post
    \\n$""", re.VERBOSE)

to_update, tot_posts = [], 0
partial_threads = "".join(sys.stdin.readlines()).split() if partial else None
todo_queue, done_queue = Queue.Queue(), Queue.Queue()

for line in subjecttxt.splitlines(True):
    line = unicode(line, "latin-1")

    try:
        thread = regex.match(line).groupdict()
        thread = dict((a, thread[a].encode('latin-1').decode(charset, 'replace'))
                      for a in thread)

        if partial and thread['id'] not in partial_threads:
            continue

        last_post = db.execute('select last_post from threads where thread = ?',
                               (thread['id'],)).fetchone()

        if last_post is None:
            # Wholly new thread
            db.execute('insert into threads values (?, ?, ?)',
                       (thread['id'], thread['subject'], 0))
            last_post = 0

        elif int(last_post[0]) < int(thread['last_post']):
            # We already have part of this thread
            last_post = db.execute('select max(id) from posts where thread = ?',
                                   (thread['id'],)).fetchone()
            last_post = last_post[0] or 0

        else:
            # Thread is up to date
            continue

        to_update.append(thread['id'])
        todo_queue.put((thread['id'], thread['last_post'], last_post + 1))
        tot_posts += int(thread['replies']) - last_post

    except:
        # Failed to parse line; skip it
        print "! subject.txt fail:", line.rstrip()

if partial and len(to_update) != len(partial_threads):
    print "Some of the threads you listed either don't need updating or",\
          "don't exist:"
        
    for thread in partial_threads:
        if thread not in to_update:
            print " ", thread

tot = len(to_update)

print "%d thread%s to update (approx. %d post%s)." % \
      (tot, '' if tot == 1 else 's', tot_posts, '' if tot_posts == 1 else 's')

if dry_run:
    print "Dry run; exiting."
    sys.exit(0)


# Fetch new posts

errors = 0

def error(message):
    global errors

    errors += 1

    print "! Error:", message
    if progress_bar:
        print


if tot > 0 and use_json:
    try:
        json_test = urlopen(json_url + to_update[0][0])
    except:
        print "Can't access JSON interface! Using HTML interface."
        use_json = False


def scrape_json():
    global todo_queue, done_queue

    # Tripcode and email, but no name
    name1 = u'^!<a href="mailto:(?P<meiru>[^"]*)">(?P<trip>![a-zA-Z0-9./]{10}|!(?:[a-zA-Z0-9./]{10})?![a-zA-Z0-9+/]{15})</a>$'
    name1 = re.compile(name1, re.DOTALL)

    # Email and name, optional tripcode
    name2 = u'^<a href="mailto:(?P<meiru>[^"]*)">(?P<name>.*?)</a>(?P<trip>![a-zA-Z0-9./]{10}|!(?:[a-zA-Z0-9./]{10})?![a-zA-Z0-9+/]{15})?$'
    name2 = re.compile(name2, re.DOTALL)

    # Ambiguous tripcode
    maybe_trip = u'^.*?!(?:[a-zA-Z0-9./]{10}|(?:[a-zA-Z0-9./]{10})?![a-zA-Z0-9+/]{15})$'
    maybe_trip = re.compile(maybe_trip, re.DOTALL)

    htripregex = u'<h3><span class="postnum"><a href=\'javascript:quote\(%s,"post1"\);\'>%s</a> </span><span class="postinfo"><span class="namelabel"> Name: </span><span class="postername">(?P<author>.*?)</span> ?<span class="postertrip">(?P<trip>.*?)</span>'

    while not todo_queue.empty():
        try:
            thread, posts = todo_queue.get(timeout=2), []
        except:
            continue

        try:
            page = urlopen(json_url + thread[0] + '/%d-' % thread[2])
        except:
            error("Can't access %s, thread skipped." % (json_url + thread[0]))
            continue

        try:
            page = json.loads(page)
        except ValueError:
            error("Can't parse JSON, thread %s skipped." % thread[0])
            continue


        # Parse names, because the JSON interface sucks

        tripv, hp = [], None

        for post in page:
            p = page[post]

            if p['name'] is None: p['name'] = u''

            m = name1.match(p['name'])

            if m is not None:
                # Tripcode and email, but no name

                for n in ('meiru', 'trip'):
                    p[n] = m.group(n)

                p['name'] = u''

            else:
                m = name2.match(p['name'])

                if m is not None:
                    # Email and name, optional tripcode

                    for n in ('meiru', 'trip', 'name'):
                        p[n] = m.group(n)

                else:
                    # Anything without e-mail

                    for n in ('meiru', 'trip'):
                        p[n] = u''

                    if maybe_trip.match(p['name']):
                        # Ambiguous tripcode

                        tripv.append(post)


        if verify_trips and len(tripv) > 0:
            tripv_url = read_url + thread[0] + '/'
            if len(tripv) < 200:
                tripv_url += ','.join(tripv)
            try:
                hp = urlopen(tripv_url)

            except:
                error("Couldn't access HTML interface to verify tripcodes. " +
                      "Skipping %s." % thread[0])
                continue


        # Verify trips if needed, insert data

        for post in page:
            p = page[post]

            if verify_trips and post in tripv:
                htripper = re.compile(htripregex % (post, post), re.DOTALL)
                m = htripper.search(hp)

                if m is None:
                    error("Malformed post header for %s! Exiting." %
                          (read_url + thread[0] + '/' + post))
                    sys.exit(1)

                else:
                    p['name'], p['trip'] = m.group('author'), m.group('trip')

            if not no_aborn or p['name'] != u'SILENT!ABORN' or \
                               p['com'] != u'SILENT' or \
                               p['now'] != u'1234':

                posts.append(map(lambda s: unicode(s, charset, "replace") \
                                          if type(s) == str else s,
                             (thread[0], post,
                              p['name'], p['meiru'], p['trip'],
                              p['now'], p['com'])))

        done_queue.put(((unicode(thread[1]), unicode(thread[0])), posts))


def scrape_html():
    global todo_queue, done_queue

    postregex = u"""\
<h3><span class="postnum"><a href='javascript:quote\((?P<id>\d+),"post1"\);'>(?P=id)</a> </span><span class="postinfo"><span class="namelabel"> Name: </span><span class="postername">(?P<author>.*?)</span><span class="postertrip">(?P<trip>.*?)</span> : <span class="posterdate">(?P<time>.*?)</span> <span class="id">.*?</span></span></h3>
<blockquote>
\t(?:<div class="aa">)?<p>
(?P<body>.*?)
\t</p>(?:</div>)?
"""
    postregex = re.compile(postregex, re.DOTALL)

    meiruregex = u'<a href="mailto:(?P<meiru>.*?)">(?P<rest>[^<]*)</a>'
    meiruregex = re.compile(meiruregex)

    while not todo_queue.empty():
        try:
            thread, posts = todo_queue.get(timeout=2), []
        except:
            continue

        try:
            page = urlopen(read_url + thread[0] + '/%d-' % thread[2])
        except:
            error("Can't access %s, skipping thread." % (read_url + thread[0]))
            continue
    
        ids, authors, emails, trips, times, bodies = [], [], [], [], [], []

        erred = False

        for p in re.split('</blockquote>', unicode(page, charset, 'replace')):
            m = postregex.search(p)
            if m is None:
                if erred:
                    error("Broken post in thread %s" % thread[0])
                erred = True
                continue

            ids.append(m.group('id'))

            meiru = False
            mm = meiruregex.match(m.group('author'))
            if mm is not None:
                authors.append(mm.group('rest'))
                emails.append(mm.group('meiru'))
                meiru = True
            else:
                authors.append(m.group('author'))

            mm = meiruregex.match(m.group('trip'))
            if not meiru and mm is not None:
                trips.append(mm.group('rest'))
                emails.append(mm.group('meiru'))
                meiru = True
            else:
                trips.append(m.group('trip'))

            if not meiru:
                emails.append('')

            times.append(int(time.mktime(time.strptime(m.group('time'),
                                                       "%Y-%m-%d %H:%M"))))

            bodies.append(m.group('body'))
    
        for post in zip(ids, authors, emails, trips, times, bodies):
            if int(post[0]) >= thread[2]:
                b = [unicode(thread[0])]
                
                for y in post:
                    if type(y) == str:
                        b.append(unicode(y, charset, 'replace'))
                    else:
                        b.append(y)

                posts.append(b)
        
        done_queue.put(((unicode(thread[1]), unicode(thread[0])), posts))


# Spawn threads

if threads < 1:
    threads = min(tot, 1000) * 31 / 1000 + 1

if tot < threads:
    threads = tot

for _ in xrange(threads):
    threading.Thread(target=scrape_json if use_json else scrape_html).start()


# Add scraped content to DB and possibly index as we're going

def show_progress(idx, tot):
    perc = idx * 100.0 / tot
    bars = "".join(map(lambda i: '#' if i <= perc else ' ', range(5, 101, 5)))

    print '\033[1AScraping... [%s] %.2f%% (%d/%d)' % (bars, perc, idx, tot)

idx = 0

if tot > 0 and progress_bar:
    print
    show_progress(idx, tot)

while threading.activeCount() > 1 or not done_queue.empty():
    try:
       thread, posts = done_queue.get(timeout=2)
    except:
        continue

    db.execute(u'update threads set last_post = ? where thread = ?', thread)

    if idir is not None:
        ixwriter = ix.writer()

    for post in posts:
        db.execute(u'insert or replace into posts \
                     (thread, id, author, email, trip, time, body) \
                     values (?, ?, ?, ?, ?, ?, ?)', post)
        if idir is not None:
            try:
                timestamp = float(post[5])
            except:
                timestamp = 0.0
            ixwriter.add_document(thread=int(post[0]), post=int(post[1]),
                                  author=scrub(post[2]), email=post[3],
                                  trip=post[4],
                                  time=datetime.fromtimestamp(timestamp),
                                  body=scrub(post[6]))

    db_conn.commit()
    if idir is not None:
        ixwriter.commit()

    idx += 1
    if progress_bar:
        show_progress(idx, tot)
    else:
        print "[%d/%d] Done thread %s." % (idx, tot, thread[1])

print "All done! Finished with %d error%s." % (errors, "" if errors == 1 else "s")

if errors > 0:
    print "It's possible that running /prog/scrape again will retrieve posts "\
          "that couldn't\nbe retrieved just now."
