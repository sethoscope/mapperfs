#!/usr/bin/env python
#
# Adapted from fusepy's loopback example

from __future__ import with_statement

from errno import EACCES, ENOENT
import os.path
from sys import argv, exit
from threading import Lock
from collections import defaultdict
import stat
import logging
import os
import time

# https://github.com/terencehonles/fusepy
from fuse import FUSE, FuseOSError, Operations, LoggingMixIn


class Directory(set):
    def num_subdirs(self):
        return sum(1 for e in self if isinstance(e, Directory))


class StaticList(LoggingMixIn, Operations):
    def __init__(self, pairs):
        '''pairs is an iterable of (realpath, mountedpath) pairs.
        For example: [('/bin/bash', '/mysteryshell'), ('/usr/bin/tcsh', '/othershell')]
        '''
        self.entries = { mounted.rstrip('/'): real.rstrip('/') for (mounted, real) in pairs }
        logging.debug('init with: ' + str(self.entries))
        self.dirs = self.synthesize_dirs(self.entries)
        self.rwlock = Lock()
        self.ctime = time.time()
        self.uid = os.geteuid()
        self.gid = os.getegid()

    @staticmethod
    def synthesize_dirs(entries):
        '''Return the directories needed to reach the entries in the form
        { path : set(contents), ... }'''
        dirs = defaultdict(Directory)
        for e in entries:
            d, base = os.path.split(e)
            while d and base and not (d in entries or (d in dirs and base in dirs[d])):
                dirs[d].add(base)
                d, base = os.path.split(d)
        logging.debug('dir tree: ' + str(dirs))
        return dirs

    def _find_referent(self, path):
        # TODO: memoize
        logging.debug('lookup: ' + path)
        if path in self.entries:
            logging.debug('  resolved %s to %s' % (path, self.entries[path]))
            return self.entries[path]
        if path in self.dirs:
            logging.debug('  resolved %s to a directory' % path)
            return self.dirs[path]
        # Perhaps it's under a directory we've exposed
        logging.debug('  could it be under a mounted directory?')
        left, base = os.path.split(path)
        right = base
        while base and not (left in self.entries or left in self.dirs):
            left, base = os.path.split(left)
            right = os.path.join(base, right)
        if left:
            logging.debug('  found %s' % left)
            if left in self.entries and os.path.isdir(self.entries[left]):
                logging.debug('  which might contain ' + right)
                return os.path.join(self.entries[left], right)
        raise FuseOSError(ENOENT)

    def __call__(self, op, path, *args):
        real_path = self._find_referent(path)
        logging.debug('calling %s with %s %s' % (op, path, str(args)))
        return Operations.__call__(self, op, real_path, *args)


    def noaccess(self, *args):
        raise FuseOSError(EACCES)

    def access(self, path, mode):
        if isinstance(path, Directory):
            return 1 if (mode & os.W_OK) else 0
        return os.access(path, mode)

    chmod = os.chmod
    chown = os.chown

    create = noaccess

    def flush(self, path, fh):
        return os.fsync(fh)

    def fsync(self, path, datasync, fh):
        return os.fsync(fh)

    def getattr(self, path, fh=None):
        if not isinstance(path, Directory):
            st = os.lstat(path)
            return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
                'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))
        logging.debug('getattr of a directory')
        return { 'st_atime' : self.ctime,
                 'st_ctime' : self.ctime,
                 'st_gid' : self.gid,
                 'st_mode' : stat.S_IFDIR | 0o555,
                 'st_mtime' : self.ctime,
                 'st_nlink' : 2 + path.num_subdirs(),
                 'st_size' : len(path),
                 'st_uid' : self.uid }

    getxattr = None
    listxattr = None

    link = noaccess
    mkdir = noaccess
    mknod = noaccess

    open = os.open

    def read(self, path, size, offset, fh):
        with self.rwlock:
            os.lseek(fh, offset, 0)
            return os.read(fh, size)

    def readdir(self, path, fh):
        if isinstance(path, Directory):
            return ['.', '..'] + list(path)
        return ['.', '..'] + os.listdir(path)

    readlink = os.readlink

    def release(self, path, fh):
        return os.close(fh)

    rename = noaccess
    rmdir = noaccess

    def statfs(self, path):
        if not isinstance(path, Directory):
            stv = os.statvfs(path)
            return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree',
                'f_blocks', 'f_bsize', 'f_favail', 'f_ffree', 'f_files', 'f_flag',
                'f_frsize', 'f_namemax'))
        return { 'f_bavail' : 0,
                 'f_bfree' : 0,
                 'f_blocks' : 0,
                 'f_bsize' : 1,
                 'f_favail' : 0,
                 'f_ffree' : 0,
                 'f_files' : 0,
                 'f_flag' : 0,
                 'f_frsize' : 0,
                 'f_namemax' : 0 }

    symlink = noaccess

    def truncate(self, path, length, fh=None):
        with open(path, 'r+') as f:
            f.truncate(length)

    unlink = noaccess
    utimens = os.utime

    def write(self, path, data, offset, fh):
        with self.rwlock:
            os.lseek(fh, offset, 0)
            return os.write(fh, data)


def read_file(filename):
    '''Yields lines from a file, ignoring lines starting with ; or #
    and removing quote marks.'''
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip(' \"\t\n')
            if not line.startswith('#') and not line.startswith(';'):
                yield line

def flatten(generator):
    for f in generator:
        f = f.rstrip('/')
        path, base = os.path.split(f)
        yield '/' + base


def longest_common_path(file_list):
    '''Return the longest path that refers to a directory under
    which all the filenames can be found.  This is similar to
    os.path.commonprefix(), but the result is guaranteed to be a
    directory, at least as implied by the filenames.
    '''
    prefix = os.path.commonprefix(file_list)
    i = prefix.rfind('/')
    if i == -1:  # not found!
        return ''
    return prefix[:i]

        
def listify(iterable):
    '''Turn iterable into a list, making a copy only if necessary.'''
    if isinstance(iterable, list):
        return iterable
    return list(iterable)


class Uncollider:
    '''When you dump files from many places into one directory
    (e.g. if you flatten the directory structure), you may have
    multiple files with the same name.  This is used to rename the
    collisions.
    '''

    fmt = '{base}-{n}{ext}'

    def uncollide(self, generator):
        # We'll go through the files in the order received, renaming any
        # that conflict with ones we've already yielded, but choosing
        # the new names such that they don't preempt any actual files
        # later in the list.  Sadly, this requires slupring up the whole
        # list at the start, which is unlikely to be an actual problem
        # for anyone, but it's nice when we can stick to O(1) generator
        # chains.
        files = listify(generator)  # alas
        reserved = set(files)
        yielded = set()
        for f in files:
            if f in yielded:
                f = self.new_name(f, reserved)
            yield f
            yielded.add(f)
            reserved.add(f)

    __call__ = uncollide

    def new_name(self, filename, reserved):
        base, ext = os.path.splitext(filename)
        n = 1
        while True:
            new_name = self.fmt.format(base=base, ext=ext, n=n)
            if new_name not in reserved:
                return new_name
            n += 1


def doubler(generator):
    for x in generator:
        yield x, x

if __name__ == '__main__':

    # I don't expect this command line application to be very useful.
    # It's more of a proof of concept and rough test.  The real value
    # will come from using this module in other code, where the files
    # and their visible mount points are based on something interesting.

    from optparse import OptionParser

    usage = '%prog <mountpoint> entries ...'
    description = 'mount a given list of files and directories'

    optparser = OptionParser(description = description,
                             usage = usage)
    optparser.add_option('-i', '--input', metavar='FILE', help='get list from FILE instead of command arguments')
    optparser.add_option('-v', '--verbose', action='store_true')
    
    schemes = [ 'copy', 'flatten', 'common']
    optparser.add_option('-s', '--scheme', type='choice',
                         choices=schemes, default=schemes[0],
                         help='choices: ' + ', '.join(schemes) + '; default: %default')
    optparser.add_option('', '--debug', action='store_true')
    (options, args) = optparser.parse_args()

    if options.verbose:
        logging.getLogger().setLevel(logging.INFO)
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if len(args) < 1:
        sys.stderr.write('usage: ' + usage.replace('%prog', argv[0]))
        sys.stderr.write('\n')
        exit(1)

    mountpoint = args.pop(0)

    if options.input:
        files = read_file(options.input)
    else:
        files = args

    if options.scheme == 'copy':
        logging.debug('copying file hierarchy')
        pairs = doubler(files)
    elif options.scheme == 'flatten':
        logging.debug('flattening!')
        files = listify(files)
        uncollide = Uncollider()
        pairs = zip(uncollide(flatten(files)), files)
    elif options.scheme == 'common':
        files = listify(files)
        prefix = longest_common_path(files)
        logging.debug('longest common prefix: ' + prefix)
        prefix_len = len(prefix)
        trimmed = (f[prefix_len:] for f in files)
        pairs = zip(trimmed, files)

    logging.debug('Mounting to ' + mountpoint)
    fuse = FUSE(StaticList(pairs), mountpoint, foreground=True)
