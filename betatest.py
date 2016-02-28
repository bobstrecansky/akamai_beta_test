#!/usr/bin/env python
# Beta test module
"""
Test an Akamai configuration.
Not intended to be especially generic or reusable.
"""

from __future__ import division, print_function
import argparse
import collections
import csv
import functools
import re
import requests
import socket
import sys
import time
from multiprocessing import Pool


# Configuration stuff
COOKIES = ({}, {'beta': 'new'}, {'legacy': 'old'})
COOKIE_NAMES = {name for d in COOKIES for name in d}

DEFAULT_HEADERS = {
    'Pragma': 'akamai-x-cache-on, akamai-x-cache-remote-on,\
               akamai-x-check-cacheable, akamai-x-get-cache-key,\
               akamai-x-get-extracted-values, akamai-x-get-nonces,\
               akamai-x-get-ssl-client-session-id,\
               akamai-x-get-true-cache-key, akamai-x-serial-no',  # noqa
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_5)\
                   AppleWebKit/537.36 (KHTML, like Gecko)\
                   Chrome/32.0.1700.77 Safari/537.36',  # noqa
}

# List of (in-test?, path) pairs
PATHS = [
    (True, "/html/solutions/index.html"),
    (True, "/html/technology/index.html"),
    (True, "/html/industry/index.html"),
]

# Definition of a diagnostic request
Request = collections.namedtuple(
    'Request', ['address', 'host', 'path', 'headers', 'cookies', 'in_test'])


# Outcome of a diagnostic request
Result = collections.namedtuple('Result', ['address',
                                           'host',
                                           'path',
                                           'sent_cookies',
                                           'status_code',
                                           'origin',
                                           'received_cookies',
                                           'content_length',
                                           'in_test',
                                           'akamai_host',
                                           'cache_hit',
                                           'cache_key',
                                           'true_cache_key',
                                           'cacheable'])


def generate_requests(paths, host, addresses=None, tests_per_path=1):
    """Return a generator of requests to send. See process_request"""
    headers = DEFAULT_HEADERS.copy()
    headers['Host'] = host
    for _ in xrange(tests_per_path):
        for in_test, path in paths:
            for cookies in COOKIES:
                for address in addresses or [None]:
                    yield Request(address=address,\
                                  host=host,\
                                  path=path,\
                                  headers=headers,\
                                  cookies=cookies,\
                                  in_test=in_test)


def hashable_cookies(_):
    """Return sorted list of cookie pairs, ignoring those not in COOKIE_NAMES"""
    return tuple(sorted((k, v) for k, v in _.items() if k in COOKIE_NAMES))


def guess_origin(_):
    """Return "new" or "old" to indicate which origin generated a response"""
    return 'new' if "Current revision:" in _.text else 'old'


def parse_x_cache(_):
    """Parse the Akamai X-Cache header, returning (result, host)"""
    regex_out = re.match(r"(\w+) from ([\w-]+)", _)
    return regex_out.groups() if regex_out else (None, None)

def process_request(_, timeout=None, delay=0):
    """Consume a Request and return a Result"""
    address = _.address or _.host
    url = "http://%s%s" % (address, _.path)
    time.sleep(delay / 1000)
    before = time.time()
    try:
        rsp = requests.get(url,\
                           headers=_.headers,\
                           cookies=_.cookies,\
                           timeout=timeout)
    except (requests.exceptions.RequestException, socket.timeout) as excp:
        elapsed = time.time() - before
        print("ERROR: " + str(excp))
        result = Result(address=_.address,\
                        host=_.host,\
                        path=_.path,\
                        sent_cookies=hashable_cookies(_.cookies),\
                        status_code="timeout",\
                        origin=None,\
                        received_cookies=None,\
                        content_length=0,\
                        in_test=_.in_test,\
                        akamai_host=None,\
                        cache_hit=None,\
                        cache_key=None,\
                        true_cache_key=None,\
                        cacheable=None)
        return result, elapsed
    elapsed = time.time() - before
    print("%s [%.2f]" % (url, elapsed))
    cache_hit = parse_x_cache(rsp.headers.get('X-Cache', ''))
    cache_key = rsp.headers.get('X-Cache-Key')
    true_cache_key = rsp.headers.get('X-True-Cache-Key')
    cacheable = rsp.headers.get('X-Check-Cacheable')
    result = Result(address=_.address,\
                    host=_.host,\
                    path=_.path,\
                    sent_cookies=hashable_cookies(_.cookies),\
                    status_code="timeout",\
                    origin=None,\
                    received_cookies=None,\
                    content_length=0,\
                    in_test=_.in_test,\
                    akamai_host=None,\
                    cache_hit=cache_hit,\
                    cache_key=cache_key,\
                    true_cache_key=true_cache_key,\
                    cacheable=cacheable)
    return result, elapsed


def analyze_result(result):
    """Return a pithy string describing goodness of the result"""
    # We always expect a successful http response
    if result.status_code != 200:
        return str(result.status_code)

    # Empty responses are always bad
    if result.content_length == 0:
        return "empty response"


def betatest():
    """Main Function Definition"""
    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--host',\
                        required=True,\
                        help="site hostname")
    parser.add_argument('--outputfile',\
                         '-o',\
                          required=True,\
                          help="write results to this file")
    parser.add_argument('--ntests',\
                        '-n',\
                         default=1,\
                         type=int,\
                         help="# of requests per path")
    parser.add_argument('--timeout',\
                        '-t',\
                         default=30,\
                         type=float,\
                         help="timeout (seconds)")
    parser.add_argument('--delay',\
                        '-d',\
                        default=0,\
                        type=float,\
                        help="wait between requests (ms)")
    parser.add_argument('--processes',\
                        '-p',\
                        default=32,\
                        type=int,\
                        help="# of parallel processes")
    parser.add_argument('--addresses',\
                        '-a',\
                        nargs='+',\
                        help="addresses to use instead of DNS")
    args = parser.parse_args()

    # Request the urls in parallel
    pool = Pool(args.processes)
    try:
        results = pool.map(functools.partial(process_request,\
                                             timeout=args.timeout,\
                                             delay=args.delay),
                           generate_requests(paths=PATHS,\
                                             host=args.host,\
                                             addresses=args.addresses,\
                                             tests_per_path=args.ntests))
    except KeyboardInterrupt:
        pool.terminate()
        sys.exit(1)

    # Group results by everything, and count
    groupby = collections.defaultdict(lambda: [0, 0.0, None])
    for result, elapsed in results:
        groupby[result][0] += 1
        groupby[result][1] += elapsed

    # Apply some heuristics to analyze each result
    for result, info in sorted(groupby.iteritems()):
        info[2] = analyze_result(result)

    # Write the results as csv to our destination fil
    with open(args.outputfile, 'wb') as file_pointer:
        writer = csv.writer(file_pointer, quoting=csv.QUOTE_ALL)
        for result, (count, elapsed, outcome) in sorted(groupby.iteritems()):
            row = list(result)
            row.append(count)
            row.append(elapsed / count)
            row.append(outcome)
            writer.writerow(row)
    return "beta test completed"

if __name__ == '__main__':
    betatest()

