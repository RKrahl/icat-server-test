"""pytest configuration.
"""

from __future__ import print_function
import sys
import os.path
from random import getrandbits
import zlib
import subprocess
import logging
from distutils.version import StrictVersion as Version
import pytest
import icat
import icat.config
from icat.ids import DataSelection
from icat.query import Query


# Note that pytest captures stderr, so we won't see any logging by
# default.  But since Suds uses logging, it's better to still have
# a well defined basic logging configuration in place.
logging.basicConfig(level=logging.INFO)

testdir = os.path.dirname(__file__)


# ============================= helper ===============================


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
    conf = getConfig(ids="optional", needlogin=False)
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

