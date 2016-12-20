#! /usr/bin/python

from distutils.core import setup
import distutils_pytest

setup(
    name = "icat-server-test",
    version = "0.1",
    description = "A test suite for ICAT and IDS servers",
    author = "Rolf Krahl",
    author_email = "rolf.krahl@helmholtz-berlin.de",
    license = "Apache-2.0",
    requires = ["icat", "pytest", "distutils_pytest"],
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
)

