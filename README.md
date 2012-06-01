motop
=====

Realtime monitoring tool for several MongoDB servers. Shows current operations ordered by durations every second.

h4. Actions

q	Quit

e	Explain the query

k   Kill operation using "mongo" executable

h4. Dependencies

* python 2.6 or greater
* pymongo 2.0 or greater [1]

[1] http://pypi.python.org/pypi/pymongo/

h4. Configuration

Configuration file can be created by copying motop.default.conf to motop.conf. Section are used for servers.

address The address of the server
