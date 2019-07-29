"""pytest configuration.
"""

from __future__ import print_function
import sys
import time
import os.path
import datetime
import re
import logging
import tempfile
import subprocess
import shutil
from distutils.version import StrictVersion as Version
import pytest
import icat
import icat.config
from icat.ids import DataSelection
from icat.query import Query
from icat.exception import stripCause
from helper import MemorySpace, StatFile


testdir = os.path.dirname(__file__)
maindir = os.path.dirname(testdir)


# ======================= logging and stats ==========================

timestamp = datetime.datetime.now().strftime("%y%m%d-%H%M%S")


logging.basicConfig(level=logging.INFO)
# Silence some rather chatty modules.
logging.getLogger('suds.client').setLevel(logging.CRITICAL)
logging.getLogger('suds').setLevel(logging.ERROR)

log = logging.getLogger("test")
logfilename = os.path.join(maindir, "test-%s.log" % timestamp)
logfile = logging.FileHandler(logfilename, mode='wt')
logformatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
logfile.setFormatter(logformatter)
log.addHandler(logfile)
log.setLevel(logging.DEBUG)
log.propagate = False


@pytest.fixture(scope="session")
def statfile(request):
    statfilename = os.path.join(maindir, "test-%s.yaml" % timestamp)
    statfile = StatFile(statfilename)
    def close():
        statfile.close()
    request.addfinalizer(close)
    return statfile

@pytest.fixture(scope="module")
def stat(statfile, request):
    def flush():
        modulename = request.node.module.__name__
        statfile.write(modulename)
    request.addfinalizer(flush)
    return statfile


# ============================= helper ===============================


def gettestdata(fname):
    fname = os.path.join(testdir, "data", fname)
    assert os.path.isfile(fname)
    return fname


def getDatasetCount(baseSize, datasetSize, numTests):
    """Try to adapt the number of test datasets to upload per test so that
    the total size of all test does not exceed baseSize.  But keep
    within some reasonable limits.
    """
    return min(max(int(baseSize / (numTests * datasetSize)), 5), 200)


def getConfig(confSection="root", **confArgs):
    """Get the configuration, skip on ConfigError.
    """
    confFile = os.path.join(testdir, "data", "icat.cfg")
    if not os.path.isfile(confFile):
        pytest.skip("no test ICAT server configured")
    try:
        confArgs['args'] = ["-c", confFile, "-s", confSection]
        config = icat.config.Config(**confArgs)
        client, conf = config.getconfig()
        conf.cmdargs = ["-c", conf.configFile[0], "-s", conf.configSection]
        return (client, conf, config)
    except icat.ConfigError as err:
        pytest.skip(err.message)


def get_icat_version():
    client, _, _ = getConfig(ids="mandatory", needlogin=False)
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


def wipe_data(client, dsquery):
    """Delete all datafiles from IDS relating to datasets matching the query.

    The argument dsquery must be a Query object searching for
    datasets.  All datafiles relating to corresponding datasets must
    have been uploaded to ids.server
    (Issue icatproject/ids.server #61).
    """
    require_ids_version("1.6.0", "Issue #42")
    if dsquery.entity.BeanName != 'Dataset':
        raise ValueError("Invalid query '%s'" % query)

    dfquery = Query(client, "Datafile", 
                    conditions={"location": "IS NOT NULL"}, limit=(0,1))
    for a,c in dsquery.conditions.items():
        dfquery.addConditions({"dataset.%s" % a: c})

    while True:
        deleteDatasets = []
        restoreDatasets = []
        for ds in client.searchChunked(dsquery):
            status = client.ids.getStatus(DataSelection([ds]))
            if status == "ONLINE":
                deleteDatasets.append(ds)
                if len(deleteDatasets) >= 200:
                    client.deleteData(deleteDatasets)
                    client.deleteMany(deleteDatasets)
                    deleteDatasets = []
            elif status == "ARCHIVED":
                if len(restoreDatasets) < 200:
                    restoreDatasets.append(ds)
        if len(deleteDatasets) > 0:
            client.deleteData(deleteDatasets)
            client.deleteMany(deleteDatasets)
        if len(restoreDatasets) > 0:
            client.ids.restore(DataSelection(restoreDatasets))
        # This whole loop may take a significant amount of time, make
        # sure our session does not time out.
        client.refresh()
        # If any Datafile is left we need to continue the loop.
        if client.search(dfquery):
            time.sleep(60)
        else:
            break


def wipe_all(client):
    """Delete all content from ICAT.
    """
    require_icat_version("4.4.0", "Need extended root permission")
    wipe_data(client, Query(client, "Dataset"))
    tables = ["Investigation", "Facility"] + client.getEntityNames()
    for t in tables:
        query = Query(client, t, limit=(0, 200))
        while True:
            objs = client.search(query)
            if not objs:
                break
            client.deleteMany(objs)


def script_cmdline(scriptname, args):
    script = os.path.join(testdir, "scripts", scriptname)
    return [sys.executable, script] + args

def callscript(scriptname, args, stdin=None, stdout=None):

    def parse_err(err):
        found_tb = False
        err.seek(0)
        while True:
            l = err.readline()
            print(l, end='', file=sys.stderr)
            if not l:
                raise ValueError("Error line not found")
            elif l.startswith(" "):
                continue
            elif l.startswith("Traceback"):
                found_tb = True
                continue
            elif found_tb:
                m = re.match(r'([A-Za-z0-9._]+):\s*(.*)', l)
                if m:
                    cls, msg = m.groups()
                    return (eval(cls), msg)
                else:
                    raise ValueError("Invalid error line '%s'" % l)

    cmd = script_cmdline(scriptname, args)
    print("\n>", *cmd)
    with tempfile.TemporaryFile(mode='w+t') as stderr:
        try:
            subprocess.check_call(cmd, 
                                  stdin=stdin, stdout=stdout, stderr=stderr)
        except subprocess.CalledProcessError as procerr:
            try:
                errcls, msg = parse_err(stderr)
            except:
                raise stripCause(procerr)
            if issubclass(errcls, icat.ServerError):
                error = { 'message': msg, 'code': "0" }
                raise stripCause(errcls(error, None))
            else:
                raise stripCause(errcls(msg))


# ============================ fixtures ==============================


class TmpDir(object):
    """Provide a temporary directory.
    """
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="icat-test-")
    def __del__(self):
        self.cleanup()
    def cleanup(self):
        if self.dir:
            shutil.rmtree(self.dir)
        self.dir = None

@pytest.fixture(scope="session")
def tmpdir(request):
    tdir = TmpDir()
    request.addfinalizer(tdir.cleanup)
    return tdir


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


testcontent = gettestdata("icatdump.yaml")

@pytest.fixture(scope="session")
def setupicat(request):
    require_icat_version("4.4.0", "need InvestigationGroup")
    client, conf, _ = getConfig(ids="optional")
    client.login(conf.auth, conf.credentials)
    wipe_all(client)
    icatingest = request.config.getini('icatingest')
    args = conf.cmdargs + ["-f", "YAML", "-i", testcontent]
    callscript(icatingest, args)
    client.logout()


# ============================= hooks ================================


def pytest_addoption(parser):
    parser.addini('icatingest', 'path to the icatingest command')
    parser.addini('incomingbase', 'base directory of the incoming storage')
    parser.addini('mainstoragebase', 'base directory of the main storage')
    parser.addini('archivestoragebase', 'base directory of the archive storage')
    parser.addini('basesize', 'base size of the tests')
    parser.addini('cleanup', 'delete uploaded data after each test')
