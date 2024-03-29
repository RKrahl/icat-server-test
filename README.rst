icat-server-test - A test suite for ICAT and IDS servers
========================================================

This package provides a collection of tests for an `ICAT`_ and IDS
service.

*Note*: This package was originally written for inhouse use at HZB,
mainly to assess the usability of the storage backend for IDS we
planned to use.  The tests were written ad hoc, some tests turned out
to be not very useful, but have been kept anyway.  There is
essentially no documentation at all.


System requirements
-------------------

Python:

+ Python 3.2 and newer.
  (The `subprocess` module on older Python versions is not thread safe.)

Required Library packages:

+ `python-icat`_ >= 0.14.0

+ `pytest`_ >= 2.8

+ `distutils-pytest`_


Configuration
-------------

You need to configure an ICAT and IDS server to be tested.  To do so,
place an icat.cfg file into tests/data.  This file must have at least
the configuration sections "root", "useroffice", "acord", "ahau",
"jbotu", "jdoe", "nbour", and "rbeck" with the options and credentials
to access the test server as the respective user.  Obviously, this
implies that your authentication plugin must also have these users
configured.  An example configuration file is provided in the main
directory.

**WARNING**: the tests are destructive!  They will delete all content
from the test server and replace it with example content.  Do not
configure the tests to access a production server!

Furthermore there are some settings in tests/pytest.ini that you might
need to tweak.


Usage
-----

Run::

     $ python setup.py test

Note that you can run the tests right out of the source directory.
You do not need to install anything from this package, in fact, there
is nothing that you could install.


Copyright and License
---------------------

Copyright 2013-2019
Helmholtz-Zentrum Berlin für Materialien und Energie GmbH

Licensed under the Apache License, Version 2.0 (the "License"); you
may not use this file except in compliance with the License.  You may
obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
implied.  See the License for the specific language governing
permissions and limitations under the License.


.. _ICAT: http://www.icatproject.org/
.. _python-icat: https://icatproject.org/user-documentation/python-icat/
.. _pytest: http://pytest.org/
.. _distutils-pytest: https://pythonhosted.org/distutils-pytest/
