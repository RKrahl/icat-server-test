"""pytest configuration.
"""

from __future__ import print_function, division
import sys
import time
from timeit import default_timer as timer
import os.path
import datetime
import logging
import re
from random import getrandbits
import zlib
import tempfile
import zipfile
import subprocess
import shutil
from distutils.version import StrictVersion as Version
import yaml
import pytest
import icat
import icat.config
from icat.ids import DataSelection
from icat.query import Query
from icat.exception import IDSDataNotOnlineError


testdir = os.path.dirname(__file__)
maindir = os.path.dirname(testdir)


# ======================= logging and stats ==========================

timestamp = datetime.datetime.now().strftime("%y%m%d-%H%M%S")


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test")
logfilename = os.path.join(maindir, "test-%s.log" % timestamp)
logfile = logging.FileHandler(logfilename, mode='wt')
logformatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
logfile.setFormatter(logformatter)
log.addHandler(logfile)
log.propagate = False


class StatItem(object):
    def __init__(self, tag, name, size, time):
        self.tag = tag
        self.name = name
        self.size = size
        self.time = time
    def as_dict(self):
        return { 'name': self.name, 'size': self.size, 'time': self.time, }

class StatFile(object):

    def __init__(self, path):
        self.stream = open(path, 'wt')
        print("%YAML 1.1\n# Test statistics", file=self.stream)
        self.stats = []

    def add(self, item):
        self.stats.append(item)

    def write(self, modulename=None):
        if self.stats:
            tags = set([ i.tag for i in self.stats ])
            stats = {}
            for t in tags:
                stats[t] = [ i.as_dict() for i in self.stats if i.tag == t ]
            data = { 'stats': stats }
            if modulename:
                data['name'] = modulename
            yaml.dump(data, self.stream, explicit_start=True)
            self.stats = []

    def close(self):
        self.write()
        self.stream.close()

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


class Unbuffered(object):
    """An output stream with disabled buffering.
    """
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def __getattr__(self, attr):
        return getattr(self.stream, attr)


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
        return str(bytearray(seq))
else:
    def buf(seq):
        return bytearray(seq)


def gettestdata(fname):
    fname = os.path.join(testdir, "data", fname)
    assert os.path.isfile(fname)
    return fname


def copyfile(infile, outfile, chunksize=8192):
    """Read all data from infile and write them to outfile.
    """
    size = 0
    while True:
        chunk = infile.read(chunksize)
        if not chunk:
            break
        outfile.write(chunk)
        size += len(chunk)
    return size


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


def callscript(scriptname, args, stdin=None, stdout=None, stderr=None):
    script = os.path.join(testdir, "scripts", scriptname)
    cmd = [sys.executable, script] + args
    print("\n>", *cmd)
    subprocess.check_call(cmd, stdin=stdin, stdout=stdout, stderr=stderr)


# =========================== test data ==============================


class DatafileBase(object):
    """Abstract base for Datafile classes.
    Derived classes must implemtent _read().
    """
    def __init__(self, size):
        self.size = size
        self._delivered = 0
        self.crc32 = 0
    def close(self):
        pass
    def _read(self, n):
        raise NotImplementedError
    def read(self, n):
        remaining = self.size - self._delivered
        if n < 0 or n > remaining:
            n = remaining
        chunk = self._read(n)
        self.crc32 = zlib.crc32(chunk, self.crc32)
        self._delivered += n
        return chunk
    def getcrc(self):
        return "%x" % (self.crc32 & 0xffffffff)

class DummyDatafile(DatafileBase):
    """A dummy readable with random or zero content.
    """
    def __init__(self, size, data):
        super(DummyDatafile, self).__init__(size)
        assert data in ['random', 'urandom', 'zero']
        if data == 'urandom':
            self.data = open('/dev/urandom', 'rb')
        else:
            self.data = data
    def close(self):
        try:
            self.data.close()
        except AttributeError:
            pass
    def _read(self, n):
        if hasattr(self.data, 'read'):
            return self.data.read(n)
        elif self.data == 'random':
            return buf(getrandbits(8) for _ in range(n))
        elif self.data == 'zero':
            return buf(n)
        else:
            raise ValueError("invalid data source '%s'" % self.data)

class DatasetBase(object):
    """A test dataset.

    Upload and download a set of random data files.
    """

    fileCount = None
    fileSize = None
    _datafileFormat = None
    _datasetType = None

    @classmethod
    def getDatafileFormat(cls, client):
        if not cls._datafileFormat:
            query = "SELECT o FROM DatafileFormat o WHERE o.name = 'raw'"
            cls._datafileFormat = client.assertedSearch(query)[0]
        return cls._datafileFormat

    @classmethod
    def getDatasetType(cls, client):
        if not cls._datasetType:
            query = "SELECT o FROM DatasetType o WHERE o.name = 'raw'"
            cls._datasetType = client.assertedSearch(query)[0]
        return cls._datasetType

    @classmethod
    def getSize(cls):
        assert cls.fileCount is not None
        assert cls.fileSize is not None
        return cls.fileCount*cls.fileSize

    def __init__(self, client, investigation, name, 
                 fileCount=None, fileSize=None, data='urandom'):
        self.name = name
        if fileCount:
            self.fileCount = fileCount
        else:
            assert self.fileCount is not None
        if fileSize:
            self.fileSize = fileSize
        else:
            assert self.fileSize is not None
        self.data = data
        self.size = self.fileCount*self.fileSize

        datasetType = self.getDatasetType(client)
        dataset = client.new("dataset", name=self.name, complete=False, 
                             investigation=investigation, type=datasetType)
        dataset.create()
        dataset.truncateRelations()
        self.dataset = dataset

    def uploadFiles(self, client):
        datafileFormat = self.getDatafileFormat(client)
        start = timer()
        for n in range(1,self.fileCount+1):
            name = "test_%05d.dat" % n
            if isinstance(self.data, str):
                f = DummyDatafile(self.fileSize, self.data)
            elif issubclass(self.data, DatafileBase):
                f = self.data(self.fileSize)
            else:
                raise TypeError("data: invalid type %s. Must either be str "
                                "or a subclass of DatafileBase." 
                                % type(self.data))
            datafile = client.new("datafile",
                                  name=name,
                                  dataset=self.dataset,
                                  datafileFormat=datafileFormat)
            df = client.putData(f, datafile)
            crc = f.getcrc()
            assert df.location is not None
            assert df.fileSize == self.fileSize
            assert df.checksum == crc
            f.close()
        end = timer()
        elapsed = Time(end - start)
        log.info("Uploaded %s to dataset %s in %s (%s/s)", 
                 self.size, self.name, elapsed, MemorySpace(self.size/elapsed))
        return StatItem("upload", self.name, int(self.size), float(elapsed))

    def download(self, client):
        query = Query(client, "Datafile", conditions={
            "dataset.id": "= %d" % self.dataset.id,
        })
        datafiles = client.search(query)
        assert len(datafiles) == self.fileCount
        with tempfile.TemporaryFile() as f:
            start = timer()
            while True:
                try:
                    response = client.getData([self.dataset])
                    break
                except IDSDataNotOnlineError:
                    log.info("Wait for dataset %s to become online.", 
                             self.name)
                    time.sleep(1)
            size = MemorySpace(copyfile(response, f))
            end = timer()
            zf = zipfile.ZipFile(f, 'r')
            zinfos = zf.infolist()
            assert len(zinfos) == len(datafiles)
            for df in datafiles:
                zi = None
                for i in zinfos:
                    if i.filename.endswith(df.name):
                        zi = i
                        break
                assert zi is not None
                assert "%x" % (zi.CRC & 0xffffffff) == df.checksum
                assert zi.file_size == df.fileSize
        elapsed = Time(end - start)
        log.info("Downloaded %s for dataset %s in %s (%s/s)", 
                 self.size, self.name, elapsed, MemorySpace(self.size/elapsed))
        return StatItem("download", self.name, int(self.size), float(elapsed))


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

