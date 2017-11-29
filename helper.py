"""Helper classes and functions needed by the tests.
"""

from __future__ import print_function, division
import sys
import re
import time
from timeit import default_timer as timer
from random import getrandbits
import tempfile
import zlib
import zipfile
import logging
import yaml
from icat.query import Query
from icat.exception import IDSDataNotOnlineError


log = logging.getLogger("test")

if sys.version_info < (3, 0):
    def buf(seq):
        return str(bytearray(seq))
else:
    def buf(seq):
        return bytearray(seq)

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
        self._delivered += len(chunk)
        return chunk
    def getcrc(self):
        return "%x" % (self.crc32 & 0xffffffff)

class DummyDatafile(DatafileBase):
    """A dummy readable with random or zero content.
    """
    def __init__(self, size, data):
        super(DummyDatafile, self).__init__(size)
        assert data in ['random', 'urandom', 'zero'], "invalid data"
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
            query = "SELECT o FROM DatafileFormat o WHERE o.name = 'Other'"
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
        assert cls.fileCount is not None, "fileCount is not set"
        assert cls.fileSize is not None, "fileSize is not set"
        return cls.fileCount*cls.fileSize

    def __init__(self, client, investigation, name, 
                 fileCount=None, fileSize=None, data='urandom'):
        self.name = name
        if fileCount:
            self.fileCount = fileCount
        else:
            assert self.fileCount is not None, "fileCount is not set"
        if fileSize:
            self.fileSize = fileSize
        else:
            assert self.fileSize is not None, "fileSize is not set"
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
            assert df.location is not None, "location is not set"
            assert df.fileSize == self.fileSize, "fileSize does not match"
            assert df.checksum == crc, "checksum does not match"
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
        assert len(datafiles) == self.fileCount, "wrong number of datafiles"
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
            assert len(zinfos) == len(datafiles), \
                "wrong number of datafiles in download"
            for df in datafiles:
                zi = None
                for i in zinfos:
                    if i.filename.endswith(df.name):
                        zi = i
                        break
                assert zi is not None, \
                    "datafile not found in download"
                assert "%x" % (zi.CRC & 0xffffffff) == df.checksum, \
                    "checksum does not match in download"
                assert zi.file_size == df.fileSize, \
                    "fileSize does not match in download"
        elapsed = Time(end - start)
        log.info("Downloaded %s for dataset %s in %s (%s/s)", 
                 self.size, self.name, elapsed, MemorySpace(self.size/elapsed))
        return StatItem("download", self.name, int(self.size), float(elapsed))
