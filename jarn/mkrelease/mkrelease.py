import sys
import os
import getopt
import tempfile
import shutil
import ConfigParser

from os.path import abspath, join, expanduser, isfile

from python import Python
from setuptools import Setuptools
from scp import SCP
from scm import SCMContainer
from exit import msg_exit, err_exit

pypiurl = "http://pypi.python.org/pypi"
maxaliasdepth = 23

version = "jarn.mkrelease 2.0b1"
usage = "Try 'mkrelease --help' for more information."
help = """\
Usage: mkrelease [options] [scm-url|scm-sandbox]

Release sdist eggs

Options:
  -C, --skip-checkin  Do not checkin modified files from the sandbox.
  -T, --skip-tag      Do not tag the release in SCM.
  -U, --skip-upload   Do not upload the release to dist-location.
  -D, --dry-run       Dry-run; equivalent to -CTU.

  --svn, --hg, --git  Select the SCM type. Only required if the SCM type
                      cannot be guessed from the argument.

  -p, --push          Push local changes upstream. Applies to Mercurial
                      and Git repositories only.

  -s, --sign          Sign the release with GnuPG.
  -i identity, --identity=identity
                      The GnuPG identity to sign with.

  -d dist-location, --dist-location=dist-location
                      An scp destination specification, or an index server
                      configured in ~/.pypirc, or an alias name for either.
                      This option may be specified more than once.

  -q, --quiet         Suppress the output of setup.py sdist.
  -k, --keep-temp     Keep the temporary build directory.
  -h, --help          Print this help message and exit.
  -v, --version       Print the version string and exit.

  scm-url             The URL of a remote SCM repository.
  scm-sandbox         A local SCM sandbox; defaults to the current working
                      directory.
"""


class Defaults(object):

    def __init__(self):
        """Read config files.
        """
        self.parser = ConfigParser.ConfigParser()
        self.parser.read((expanduser('~/.pypirc'), '/etc/mkrelease',
                          expanduser('~/.mkrelease')))

        def get(section, key, default=None):
            if self.parser.has_option(section, key):
                return self.parser.get(section, key)
            return default

        self.python = sys.executable
        self.distbase = get('defaults', 'distbase', '')
        self.distdefault = get('defaults', 'distdefault', '')

        self.aliases = {}
        if self.parser.has_section('aliases'):
            for key, value in self.parser.items('aliases'):
                self.aliases[key] = value.split()

        self.servers = {}
        for server in get('distutils', 'index-servers', '').split():
            self.servers[server] = True
            url = get(server, 'repository', pypiurl)
            self.servers[url] = True


class Locations(object):

    def __init__(self, defaults):
        self.distbase = defaults.distbase
        self.distdefault = defaults.distdefault
        self.aliases = defaults.aliases
        self.servers = defaults.servers
        self.locations = []

    def __len__(self):
        """Return number of locations.
        """
        return len(self.locations)

    def __iter__(self):
        """Iterate over locations.
        """
        return iter(self.locations)

    def extend(self, location):
        """Extend list of locations.
        """
        self.locations.extend(location)

    def is_server(self, location):
        """Return True if 'location' is an index server.
        """
        return location in self.servers

    def has_host(self, location):
        """Return True if 'location' contains a host part.
        """
        colon = location.find(':')
        slash = location.find('/')
        return colon > 0 and (slash < 0 or slash > colon)

    def get_location(self, location, depth=0):
        """Resolve aliases and apply distbase.
        """
        if not location:
            return []
        if location in self.aliases:
            res = []
            if depth > maxaliasdepth:
                err_exit('Maximum alias depth exceeded: %(location)s' % locals())
            for loc in self.aliases[location]:
                res.extend(self.get_location(loc, depth+1))
            return res
        if self.is_server(location):
            return [location]
        if not self.has_host(location) and self.distbase:
            sep = '/'
            if self.distbase[-1] in (':', '/'):
                sep = ''
            return [self.distbase + sep + location]
        return [location]

    def default_location(self):
        """Return the default location.
        """
        return self.get_location(self.distdefault)

    def check_valid_locations(self, locations=None):
        """Fail if 'locations' is empty or contains bad scp destinations.
        """
        if locations is None:
            locations = self.locations
        if not locations:
            err_exit('mkrelease: option -d is required\n%s' % usage)
        for location in locations:
            if not self.is_server(location) and not self.has_host(location):
                err_exit('Scp destination must contain host part: %(location)s' % locals())


class ReleaseMaker(object):

    def __init__(self, args):
        """Set defaults.
        """
        self.skipcheckin = False
        self.skiptag = False
        self.skipupload = False
        self.keeptemp = False
        self.push = False
        self.quiet = False
        self.sdistflags = ['--formats="zip"']
        self.uploadflags = []
        self.directory = os.curdir
        self.defaults = Defaults()
        self.locations = Locations(self.defaults)
        self.python = Python(self.defaults)
        self.setuptools = Setuptools(self.defaults)
        self.scp = SCP()
        self.scmcontainer = SCMContainer()
        self.scm = None
        self.scmtype = ''
        self.args = args

    def get_options(self):
        """Parse command line.
        """
        try:
            options, args = getopt.getopt(self.args, 'CDTUd:hi:kpqsv',
                ('skip-checkin', 'skip-tag', 'skip-upload', 'dry-run', 'keep-temp',
                 'sign', 'identity=', 'dist-location=', 'version', 'help',
                 'push', 'quiet', 'svn', 'hg', 'git'))
        except getopt.GetoptError, e:
            err_exit('mkrelease: %s\n%s' % (e.msg, usage))

        for name, value in options:
            if name in ('-C', '--skip-checkin'):
                self.skipcheckin = True
            elif name in ('-T', '--skip-tag'):
                self.skiptag = True
            elif name in ('-U', '--skip-upload'):
                self.skipupload = True
            elif name in ('-D', '--dry-run'):
                self.skipcheckin = self.skiptag = self.skipupload = True
            elif name in ('-k', '--keep-temp'):
                self.keeptemp = True
            elif name in ('-p', '--push'):
                self.push = True
            elif name in ('-q', '--quiet'):
                self.quiet = True
            elif name in ('-s', '--sign'):
                self.uploadflags.append('--sign')
            elif name in ('-i', '--identity'):
                self.uploadflags.append('--identity="%s"' % value)
            elif name in ('-d', '--dist-location'):
                self.locations.extend(self.locations.get_location(value))
            elif name in ('-v', '--version'):
                msg_exit(version)
            elif name in ('-h', '--help'):
                msg_exit(help)
            elif name in ('--svn', '--hg', '--git'):
                self.scmtype = name[2:]

        if self.uploadflags and '--sign' not in self.uploadflags:
            self.uploadflags.append('--sign')

        if not self.locations:
            self.locations.extend(self.locations.default_location())

        if not self.skipupload:
            self.locations.check_valid_locations()

        if len(args) > 1:
            err_exit('mkrelease: too many arguments\n%s' % usage)

        if args:
            self.directory = args[0]

    def get_packageurl(self):
        """Get URL to release.
        """
        directory = self.directory
        self.scm = self.scmcontainer.guess_scm(self.scmtype, directory)

        if self.scm.is_valid_url(directory):
            self.remoteurl = directory
            self.isremote = True
            self.push = True
        else:
            directory = abspath(directory)
            self.scm.check_valid_sandbox(directory)
            self.setuptools.check_valid_package(directory)

            self.remoteurl = self.scm.get_url_from_sandbox(directory)
            self.isremote = False

            name, version = self.setuptools.get_package_info(directory)
            print 'Releasing', name, version

            if not self.skipcheckin:
                if self.scm.is_dirty_sandbox(directory):
                    self.scm.checkin_sandbox(directory, name, version, self.push)

    def make_release(self):
        """Build and distribute the egg.
        """
        tempdir = abspath(tempfile.mkdtemp(prefix='mkrelease-'))
        directory = join(tempdir, 'checkout')
        scmtype = self.scm.name
        sdistflags = self.sdistflags
        uploadflags = self.uploadflags

        try:
            if self.isremote:
                self.scm.checkout_url(self.remoteurl, directory)
            else:
                directory = abspath(self.directory)

            self.scm.check_valid_sandbox(directory)
            self.setuptools.check_valid_package(directory)
            self.scm.check_dirty_sandbox(directory)
            self.scm.check_unclean_sandbox(directory)

            name, version = self.setuptools.get_package_info(directory)
            if self.isremote:
                print 'Releasing', name, version

            if not self.skiptag:
                tagid = self.scm.get_tag_id(directory, version)
                self.scm.check_tag_exists(directory, tagid)
                print 'Tagging', name, version
                self.scm.create_tag(directory, tagid, name, version, self.push)

            distfile = self.setuptools.run_sdist(directory, sdistflags, scmtype, self.quiet)

            if not self.skipupload:
                for location in self.locations:
                    if self.locations.is_server(location):
                        if '--sign' in uploadflags:
                            if isfile(distfile+'.asc'):
                                os.remove(distfile+'.asc')
                        self.setuptools.run_upload(directory, location, sdistflags, uploadflags, scmtype)
                    else:
                        self.scp.run_scp(distfile, location)
        finally:
            if not self.keeptemp:
                shutil.rmtree(tempdir)

    def run(self):
        self.python.check_valid_python()
        self.get_options()
        self.get_packageurl()
        self.make_release()
        print 'done'


def main(args=None):
    if args is None:
        args = sys.argv[1:]
    try:
        ReleaseMaker(args).run()
    except SystemExit, e:
        return e.code
    return 0


if __name__ == '__main__':
    sys.exit(main())

