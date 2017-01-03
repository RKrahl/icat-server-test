"""pytest configuration.
"""

from __future__ import print_function, division
import sys
import time
import os.path
import datetime
import logging
import re
from random import getrandbits
import zlib
import subprocess
from distutils.version import StrictVersion as Version
import pytest
import icat
import icat.config
from icat.ids import DataSelection
from icat.query import Query


testdir = os.path.dirname(__file__)
maindir = os.path.dirname(testdir)


# ============================ logging ===============================


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test")
timestamp = datetime.datetime.now().strftime("%y%m%d-%H%M%S")
logfilename = os.path.join(maindir, "test-%s.log" % timestamp)
logfile = logging.FileHandler(logfilename, mode='wt')
logfile.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
logger.addHandler(logfile)
logger.propagate = False


# ============================= helper ===============================


class Time(float):
    """Convenience: human readable time intervals.
    """
    second = 1
    minute = 60*second
    hour = 60*minute
    day = 24*hour
    millisecond = (1/1000)*second
    units = { 'ms':millisecond, 's':second, 'min':minute, 'h':hour, 'd':day, }

    def __new__(cls, value):
        if isinstance(value, str):
            m = re.match(r'^(\d+(?:\.\d+)?)\s*(ms|s|min|h|d)$', value)
            if not m:
                raise ValueError("Invalid time string '%s'" % value)
            v = float(m.group(1)) * cls.units[m.group(2)]
            return super(Time, cls).__new__(cls, v)
        else:
            v = float(value)
            if v < 0:
                raise ValueError("Invalid time value %f" % v)
            return super(Time, cls).__new__(cls, v)

    def __str__(self):
        for u in ['d', 'h', 'min', 's']:
            if self >= self.units[u]:
                return "%.3f %s" % (self / self.units[u], u)
        else:
            return "%.3f ms" % (self / self.units['ms'])

class MemorySpace(int):
    """Convenience: human readable amounts of memory space.
    """
    sizeB = 1
    sizeKiB = 1024*sizeB
    sizeMiB = 1024*sizeKiB
    sizeGiB = 1024*sizeMiB
    sizeTiB = 1024*sizeGiB
    sizePiB = 1024*sizeTiB
    units = { 'B':sizeB, 'KiB':sizeKiB, 'MiB':sizeMiB, 
              'GiB':sizeGiB, 'TiB':sizeTiB, 'PiB':sizePiB, }

    def __new__(cls, value):
        if isinstance(value, str):
            m = re.match(r'^(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB|PiB)$', value)
            if not m:
                raise ValueError("Invalid size string '%s'" % value)
            v = float(m.group(1)) * cls.units[m.group(2)]
            return super(MemorySpace, cls).__new__(cls, v)
        else:
            v = int(value)
            if v < 0:
                raise ValueError("Invalid size value %d" % v)
            return super(MemorySpace, cls).__new__(cls, v)

    def __str__(self):
        for u in ['PiB', 'TiB', 'GiB', 'MiB', 'KiB']:
            if self >= self.units[u]:
                return "%.2f %s" % (self / self.units[u], u)
        else:
            return "%d B" % (int(self))

    def __rmul__(self, other):
        if type(other) == int:
            return MemorySpace(other*int(self))
        else:
            return super(MemorySpace, self).__rmul__(self, other)


if sys.version_info < (3, 0):
    def buf(seq):
        return buffer(bytearray(seq))
else:
    def buf(seq):
        return bytearray(seq)

class DummyDatafile(object):
    """A dummy readable with random content to be used for test upload.
    """
    def __init__(self, size):
        self.size = size
        self._delivered = 0
        self.crc32 = 0
    def read(self, n):
        remaining = self.size - self._delivered
        if n < 0 or n > remaining:
            n = remaining
        chunk = buf(getrandbits(8) for _ in range(n))
        self.crc32 = zlib.crc32(chunk, self.crc32)
        self._delivered += n
        return chunk
    def getcrc(self):
        return "%x" % (self.crc32 & 0xffffffff)


def gettestdata(fname):
    fname = os.path.join(testdir, "data", fname)
    assert os.path.isfile(fname)
    return fname


def getConfig(confSection="root", **confArgs):
    """Get the configuration, skip on ConfigError.
    """
    confFile = os.path.join(testdir, "data", "icat.cfg")
    if not os.path.isfile(confFile):
        pytest.skip("no test ICAT server configured")
    try:
        args = ["-c", confFile, "-s", confSection]
        conf = icat.config.Config(**confArgs).getconfig(args)
        conf.cmdargs = ["-c", conf.configFile[0], "-s", conf.configSection]
        return conf
    except icat.ConfigError as err:
        pytest.skip(err.message)


def get_icat_version():
    conf = getConfig(ids="mandatory", needlogin=False)
    client = icat.Client(conf.url, **conf.client_kwargs)
    return (client.apiversion, client.ids.apiversion)

# ICAT server version we talk to.  Ignore any errors from
# get_icat_version(), if something fails (e.g. no server is configured
# at all), set a dummy zero version number.
try:
    icat_version, ids_version = get_icat_version()
except:
    icat_version, ids_version = (Version("0.0"), Version("0.0"))

def require_icat_version(minversion, reason):
    if icat_version < minversion:
        pytest.skip("need ICAT server version %s or newer: %s" 
                    % (minversion, reason))

def require_ids_version(minversion, reason):
    if ids_version < minversion:
        pytest.skip("need IDS server version %s or newer: %s" 
                    % (minversion, reason))


def wipe_datafiles(client, query):
    """Delete all datafiles from IDS that match the query.
    """
    require_ids_version("1.3.0", "Issue #14")
    while True:
        for df in client.searchChunked(query):
            selection = DataSelection([df])
            if client.ids.getStatus(selection) == "ONLINE":
                client.deleteData(selection)
        for df in client.searchChunked(query):
            selection = DataSelection([df])
            if client.ids.getStatus(selection) == "ARCHIVED":
                client.ids.restore(selection)
        q = query.copy()
        q.setLimit( (0,1) )
        if client.search(q):
            time.sleep(10)
        else:
            break

def wipe_all(client):
    """Delete all content from ICAT.
    """
    require_icat_version("4.4.0", "Need extended root permission")
    query = Query(client, "Datafile", conditions={"location": "IS NOT NULL"})
    wipe_datafiles(client, query)
    tables = ["Investigation", "Facility"] + client.getEntityNames()
    for t in tables:
        query = Query(client, t, limit=(0, 200))
        while True:
            objs = client.search(query)
            if not objs:
                break
            client.deleteMany(objs)


def callscript(scriptname, args, stdin=None, stdout=None, stderr=None):
    script = os.path.join(testdir, "scripts", scriptname)
    cmd = [sys.executable, script] + args
    print("\n>", *cmd)
    subprocess.check_call(cmd, stdin=stdin, stdout=stdout, stderr=stderr)


# ============================ fixtures ==============================


class TestConfig(object):
    """Define a name space to hold configuration parameter."""
    pass

@pytest.fixture(scope="module")
def testConfig(request):
    config = request.config
    conf = TestConfig()
    conf.moduleName = request.node.module.__name__
    conf.baseSize = MemorySpace(config.getini('basesize'))
    conf.cleanup = icat.config.boolean(config.getini('cleanup'))
    return conf


@pytest.fixture(scope="session")
def standardConfig():
    return getConfig(ids="optional")


testcontent = gettestdata("icatdump.yaml")

@pytest.fixture(scope="session")
def setupicat(standardConfig, request):
    require_icat_version("4.4.0", "need InvestigationGroup")
    client = icat.Client(standardConfig.url, **standardConfig.client_kwargs)
    client.login(standardConfig.auth, standardConfig.credentials)
    wipe_all(client)
    icatingest = request.config.getini('icatingest')
    args = standardConfig.cmdargs + ["-f", "YAML", "-i", testcontent]
    callscript(icatingest, args)


# ============================= hooks ================================


def pytest_addoption(parser):
    parser.addini('icatingest', 'path to the icatingest command')
    parser.addini('basesize', 'base size of the tests')
    parser.addini('cleanup', 'delete uploaded data after each test')

