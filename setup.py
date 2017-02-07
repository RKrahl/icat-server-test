#! /usr/bin/python

from distutils.core import setup
from distutils import log
import distutils.command.install
import subprocess
import distutils_pytest

# Do nothing dummy.
class install(distutils.command.install.install):
    def run(self):
        """Runs the command."""
        log.info("there is nothing to install here.")

gitcmd = ["git", "describe", "--always", "--dirty"]
proc = subprocess.Popen(["git", "describe", "--always", "--dirty"], 
                        stdout=subprocess.PIPE)
version = proc.communicate()[0].strip().decode('ascii')

setup(
    name = "icat-server-test",
    version = version,
    description = "A test suite for ICAT and IDS servers",
    author = "Rolf Krahl",
    author_email = "rolf.krahl@helmholtz-berlin.de",
    license = "Apache-2.0",
    requires = ["icat", "pytest", "distutils_pytest"],
    py_modules = ["helper"],
    classifiers = [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.6",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.1",
        "Programming Language :: Python :: 3.2",
        "Programming Language :: Python :: 3.3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Testing",
        ],
    cmdclass = {'install': install},
)

