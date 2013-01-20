#!/usr/bin/env python
# -*- coding: utf-8 -*-
##
# motop - Unix "top" Clone for MongoDB
#
# Copyright (c) 2012, Tart İnternet Teknolojileri Ticaret AŞ
#
# Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby
# granted, provided that the above copyright notice and this permission notice appear in all copies.
# 
# The software is provided "as is" and the author disclaims all warranties with regard to the software including all
# implied warranties of merchantability and fitness. In no event shall the author be liable for any special, direct,
# indirect, or consequential damages or any damages whatsoever resulting from loss of use, data or profits, whether
# in an action of contract, negligence or other tortious action, arising out of or in connection with the use or
# performance of this software.
##

"""Imports for Python 3 compatibility."""
from __future__ import print_function
try:
    import __builtin__
    __builtin__.input = __builtin__.raw_input
except ImportError: pass

"""Common library imports"""
import sys
import os
import tty
import termios
import struct
import fcntl
import select
import json
import signal
import pymongo
from bson import json_util
from time import sleep
from datetime import datetime, timedelta

class Value (int):
    """Class extents int to show big numbers human readable."""
    def __str__ (self):
        if self > 10 ** 12:
            return str (int (round (self / 10 ** 12))) + 'T'
        if self > 10 ** 9:
            return str (int (round (self / 10 ** 9))) + 'G'
        if self > 10 ** 6:
            return str (int (round (self / 10 ** 6))) + 'M'
        if self > 10 ** 3:
            return str (int (round (self / 10 ** 3))) + 'K'
        return int.__str__ (self)

class Block:
    """Class to print blocks of ordered printables."""
    def __init__ (self, *columnHeaders):
        self.__columnHeaders = columnHeaders
        self.__columnWidths = [6] * len (self.__columnHeaders)

    def reset (self, printables):
        self.__lines = []
        self.__lineClass = None
        for printable in printables:
            if not self.__lineClass:
                assert hasattr (printable, 'line')
                self.__lineClass = printable.__class__
            else:
                assert isinstance (printable, self.__lineClass)
            self.__lines.append (printable.line ())

    def __len__ (self):
        """Return line count plus one for header, one for blank line at bottom."""
        return len (self.__lines) + 2

    def __printLine (self, line, width, bold = False):
        """Print the cells separated by 2 spaces, cut the part after the width."""
        for index, cell in enumerate (line):
            if width < len (self.__columnHeaders [index]):
                break
            cell = str (cell) if cell is not None else ''
            self.__columnWidths [index] = min (width, max (len (cell) + 2, self.__columnWidths [index]))
            if bold and sys.stdout.isatty ():
                print ('\x1b[1m', end = '')
            print (cell.ljust (self.__columnWidths [index]) [:self.__columnWidths [index]], end = '')
            if bold and sys.stdout.isatty ():
                print ('\x1b[0m', end = '')
            width -= self.__columnWidths [index]
        print ()

    def printLines (self, height, width):
        """Print the lines set with reset, cuts the ones after the height."""
        assert height > 1
        self.__printLine (self.__columnHeaders, width, True)
        height -= 1
        for line in self.__lines:
            if height <= 1:
                break
            assert len (line) <= len (self.__columnHeaders)
            height -= 1
            self.__printLine (line, width)

    def findLines (self, condition):
        """Return the printables from self.__lineClass saved with reset."""
        return [self.__lineClass (*line) for line in self.__lines if condition (line)]

class ServerStatus:
    def __init__ (self, server, **status):
        self.__server = server
        self.__status = status

    block = Block ('Server', 'QPS', 'Client', 'Queue', 'Flush', 'Connection', 'Memory', 'Network I/O')

    def line (self):
        cells = []
        cells.append (str (self.__server))
        cells.append (Value (self.__status ['qPS']))
        cells.append (Value (self.__status ['activeClients']))
        cells.append (Value (self.__status ['currentQueue']))
        cells.append (Value (self.__status ['flushes']))
        cells.append ('{0} / {1}'.format (Value (self.__status ['currentConn']), Value (self.__status ['totalConn'])))
        cells.append ('{0} / {1}'.format (Value (self.__status ['residentMem']), Value (self.__status ['mappedMem'])))
        cells.append ('{0} / {1}'.format (Value (self.__status ['bytesIn']), Value (self.__status ['bytesOut'])))
        return cells

class ReplicationInfo:
    def __init__ (self, server, source, syncedTo):
        self.__server = server
        self.__source = source
        self.__syncedTo = syncedTo

    block = Block ('Server', 'Source', 'SyncedTo')

    def line (self):
        return self.__server, self.__source, self.__syncedTo.as_datetime ()

class ReplicaSetMember:
    def __init__ (self, replicaSet, name, state, uptime, lag, increment, ping, server = None):
        self.__replicaSet = replicaSet
        self.__name = name
        self.__state = state.lower ()
        self.__uptime = uptime
        self.__lag = lag
        self.__increment = increment
        self.__ping = ping
        self.__server = server

    def __str__ (self):
        return self.__name

    def revise (self, otherMember):
        """Merge properties of the other replica set member with following rules."""
        if otherMember.__uptime is not None:
            if self.__uptime is None or self.__uptime < otherMember.__uptime:
                self.__uptime = otherMember.__uptime
        if otherMember.__replicaSet.masterState ():
            self.__lag = otherMember.__lag
        if self.__increment < otherMember.__increment:
            self.__increment = otherMember.__increment
        if otherMember.__ping is not None:
            if self.__ping is None or self.__ping < otherMember.__ping:
                self.__ping = otherMember.__ping
        if otherMember.__server is not None and self.__server is None:
            self.__server = otherMember.__server

    block = Block ('Server', 'Set', 'State', 'Uptime', 'Lag', 'Inc', 'Ping')

    def line (self):
        cells = []
        cells.append (str (self.__server) if self.__server else self.__name)
        cells.append (str (self.__replicaSet))
        cells.append (self.__state)
        cells.append (self.__uptime)
        cells.append (self.__lag)
        cells.append (self.__increment)
        cells.append (self.__ping)
        return cells

class ReplicaSet:
    def __init__ (self, name, state):
        self.__name = name
        self.__state = state
        self.__members = []

    def __str__ (self):
        return self.__name

    def masterState (self):
        return self.__state == 1

    def addMember (self, *args):
        self.__members.append (ReplicaSetMember (self, *args))

    def members (self):
        return self.__members

    def findMember (self, name):
        for member in self.__members:
            if str (member) == name:
                return member

    def revise (self, other):
        for member in self.__members:
            member.revise (other.findMember (str (member)))

class Operation:
    def __init__ (self, server, opid, state, duration = None, namespace = None, query = None):
        self.__server = server
        self.__opid = opid
        self.__state = state
        self.__duration = duration
        self.__namespace = namespace
        if isinstance (query, str) and query [0] == '{' and query [-1] == '}':
            self.__query = json.loads (query, object_hook = json_util.object_hook)
        else:
            self.__query = query

    def sortOrder (self):
        return self.__duration if self.__duration is not None else -1

    block = Block ('Server', 'Opid', 'State', 'Sec', 'Namespace', 'Query')

    def line (self):
        cells = []
        cells.append (self.__server)
        cells.append (self.__opid)
        cells.append (self.__state)
        cells.append (self.__duration)
        cells.append (self.__namespace)
        if self.__query:
            if '$msg' in self.__query:
                cells.append (self.__query ['$msg'])
            else:
                cells.append (json.dumps (self.__query, default = json_util.default))
        return cells

    def kill (self):
        return self.__server.killOperation (self.__opid)

    def executable (self):
        return isinstance (self.__query, dict) and self.__namespace and self.__query

    def __queryParts (self):
        """Translate query parts to arguments of pymongo find method."""
        assert isinstance (self.__query, dict)
        if any ([key in ('query', '$query') for key in self.__query.keys ()]):
            queryParts = {}
            for key, value in self.__query.items ():
                if key in ('query', '$query'):
                    queryParts ['spec'] = value
                elif key in ('explain', '$explain'):
                    queryParts ['explain'] = True
                elif key in ('orderby', '$orderby'):
                    queryParts ['sort'] = [(key, value) for key, value in value.items ()]
                else:
                    raise Exception ('Unknown query part: ' + key)
            return queryParts
        return {'spec': self.__query}

    def explain (self):
        """Print the output of the explain command executed on the server."""
        databaseName, collectionName = self.__namespace.split ('.', 1)
        queryParts = self.__queryParts ()
        for key, value in queryParts.items ():
            print (key.title () + ':', end = ' ')
            if isinstance (value, list):
                print (', '.join ([pair [0] + ': ' + str (pair [1]) for pair in value]))
            elif isinstance (value, dict):
                print (json.dumps (value, default = json_util.default, indent = 4))
            else:
                print (value)
        assert 'explain' not in queryParts
        explainOutput = self.__server.explainQuery (databaseName, collectionName, **queryParts)
        print ('Cursor:', explainOutput ['cursor'])
        print ('Indexes:', end = ' ')
        for index in explainOutput ['indexBounds']:
            print (index, end = ' ')
        print ()
        print ('IndexOnly:', explainOutput ['indexOnly'])
        print ('MultiKey:', explainOutput ['isMultiKey'])
        print ('Miliseconds:', explainOutput ['millis'])
        print ('Documents:', explainOutput ['n'])
        print ('ChunkSkips:', explainOutput ['nChunkSkips'])
        print ('Yields:', explainOutput ['nYields'])
        print ('Scanned:', explainOutput ['nscanned'])
        print ('ScannedObjects:', explainOutput ['nscannedObjects'])
        if 'scanAndOrder' in explainOutput:
            print ('ScanAndOrder:', explainOutput ['scanAndOrder'])

class ExecuteFailure (Exception):
    def __init__ (self, procedure):
        self.__procedure = procedure

    def __str__ (self):
        return str (self.__procedure)

class Server:
    defaultPort = 27017
    readPreference = pymongo.ReadPreference.SECONDARY

    def __connect (self):
        if pymongo.version_tuple >= (2, 4):
            self.__connection = pymongo.MongoClient (self.__address, read_preference = self.readPreference)
        else:
            self.__connection = pymongo.Connection (self.__address, read_preference = self.readPreference)
        if self.__username and self.__password:
            self.__connection.admin.authenticate (self.__username, self.__password)

    def __init__ (self, name, address = None, username = None, password = None, hideReplicationOperations = False):
        self.__name = name
        self.__address = address or name
        if ':' not in self.__address:
            self.__address += ':' + str (self.defaultPort)
        self.__username = username
        self.__password = password
        self.__oldValues = {}
        self.__connect ()

    def __str__ (self):
        return self.__name

    def __execute (self, procedure, *args, **kwargs):
        """Try 10 times to execute the procedure."""
        tryCount = 1
        while True:
            try:
                return procedure (*args, **kwargs)
            except pymongo.errors.AutoReconnect:
                tryCount += 1
                if tryCount >= 10:
                    raise ExecuteFailure (procedure)
                sleep (0.1)
            except pymongo.errors.OperationFailure:
                raise ExecuteFailure (procedure)

    def __statusChangePerSecond (self, name, value):
        """Calculate the difference of the value in one second with the last time by using time difference calculated
        on __getStatus."""
        oldValue = self.__oldValues [name] if name in self.__oldValues else None
        self.__oldValues [name] = value
        if oldValue:
            timespanSeconds = self.__timespan.seconds + (self.__timespan.microseconds / (10.0 ** 6))
            return (value - oldValue) / timespanSeconds
        return 0

    def status (self):
        """Get serverStatus from MongoDB, calculate time difference with the last time. Return ServerStatus object."""
        status = self.__execute (self.__connection.admin.command, 'serverStatus')
        oldCheckTime = self.__oldValues ['checkTime'] if 'checkTime' in self.__oldValues else None
        self.__oldValues ['checkTime'] = datetime.now ()
        if oldCheckTime:
            self.__timespan = self.__oldValues ['checkTime'] - oldCheckTime
        values = {}
        opcounters = status ['opcounters']
        values ['qPS'] = self.__statusChangePerSecond ('qPS', sum (opcounters.values ()))
        values ['activeClients'] = status ['globalLock'] ['activeClients'] ['total']
        values ['currentQueue'] = status ['globalLock'] ['currentQueue'] ['total']
        values ['flushes'] = self.__statusChangePerSecond ('flushes', status ['backgroundFlushing'] ['flushes'])
        values ['currentConn'] = status ['connections'] ['current']
        values ['totalConn'] = status ['connections'] ['available'] + status ['connections'] ['current']
        values ['residentMem'] = status ['mem'] ['resident'] * (10 ** 6)
        values ['mappedMem'] = status ['mem'] ['mapped'] * (10 ** 6)
        values ['bytesIn'] = self.__statusChangePerSecond ('bytesIn', status ['network'] ['bytesIn'])
        values ['bytesOut'] = self.__statusChangePerSecond ('bytesOut', status ['network'] ['bytesOut'])
        return ServerStatus (self, **values)

    def replicationInfo (self):
        """Find replication source from the local collection."""
        sources = self.__execute (self.__connection.local.sources.find)
        for source in sources:
            return ReplicationInfo (self, source ['host'], source ['syncedTo'])

    def replicaSet (self):
        """Execute replSetGetStatus operation on the server. Filter arbiters. Calculate the lag. Add relation to the
        member which is the server itself. Return the replica set."""
        replicaSetStatus = self.__execute (self.__connection.admin.command, 'replSetGetStatus')
        replicaSet = ReplicaSet (replicaSetStatus ['set'], replicaSetStatus ['myState'])
        for member in replicaSetStatus ['members']:
            if 'statusStr' not in member or member ['statusStr'] not in ['ARBITER']:
                uptime = timedelta (seconds = member ['uptime']) if 'uptime' in member else None
                ping = member ['pingMs'] if 'pingMs' in member else None
                lag = replicaSetStatus ['date'] - member ['optimeDate']
                optime = member ['optime']
                if member ['name'] == self.__address:
                    replicaSet.addMember (member ['name'], member ['stateStr'], uptime, lag, optime.inc, ping, self)
                else:
                    replicaSet.addMember (member ['name'], member ['stateStr'], uptime, lag, optime.inc, ping)
        return replicaSet

    def currentOperations (self, hideReplicationOperations = False):
        """Execute currentOp operation on the server. Filter and yield returning operations."""
        for op in self.__execute (self.__connection.admin.current_op) ['inprog']:
            if hideReplicationOperations:
                if op ['op'] == 'getmore' and 'local.oplog.' in op ['ns']:
                    """Condition to find replication operation on the master."""
                    continue
                if op ['op'] and op ['ns'] in ('', 'local.sources'):
                    """Condition to find replication operation on the slave. Do not look for more replication
                    operations if one found."""
                    continue
            duration = op ['secs_running'] if 'secs_running' in op else None
            yield Operation (self, op ['opid'], op ['op'], duration, op ['ns'], op ['query'] or None)

    def explainQuery (self, databaseName, collectionName, **kwargs):
        collection = getattr (getattr (self.__connection, databaseName), collectionName)
        cursor = self.__execute (collection.find, **kwargs)
        return self.__execute (cursor.explain)

    def killOperation (self, opid):
        """Kill operation using the "mongo" executable on the shell. That is because I could not make it with
        pymongo."""
        command = "echo 'db.killOp ({0})' | mongo".format (str (opid))
        command += ' ' + self.__address + '/admin'
        if self.__username:
            command += ' --username ' + self.__username
        if self.__password:
            command += ' --password ' + self.__password
        os.system (command)

class ConsoleActivator:
    """Class to use with "with" statement to hide pressed buttons on the console."""
    def __enter__ (self):
        try:
            self.__settings = termios.tcgetattr (sys.stdin)
            tty.setcbreak (sys.stdin.fileno())
        except termios.error:
            self.__settings = None
        return Console (self)

    def __exit__ (self, *ignored):
        if self.__settings:
            termios.tcsetattr (sys.stdin, termios.TCSADRAIN, self.__settings)

class ConsoleDeactivator ():
    """Class to use with "with" statement as "wihout" statement for ConsoleActivator."""
    def __init__ (self, consoleActivator):
        self.__consoleActivator = consoleActivator

    def __enter__ (self):
        self.__consoleActivator.__exit__ ()

    def __exit__ (self, *ignored):
        self.__consoleActivator.__enter__ ()

class Console:
    """Main class for input and output."""
    def __init__ (self, consoleActivator):
        self.__consoleDeactivator = ConsoleDeactivator (consoleActivator)
        self.__saveSize ()
        signal.signal (signal.SIGWINCH, self.__saveSize)

    def __saveSize (self, *ignored):
        try:
            self.__height, self.__width = struct.unpack ('hhhh', fcntl.ioctl(0, termios.TIOCGWINSZ , '\000' * 8)) [:2]
        except IOError:
            self.__height, self.__width = 20, 80

    def checkButton (self, waitTime = None):
        """Check one character input. Waits for approximately waitTime parameter as seconds. Wait for input if no
        parameter given."""
        if waitTime:
            while waitTime > 0:
                waitTime -= 0.1
                sleep (0.1)
                if select.select ([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                    return sys.stdin.read (1)
        else:
            return sys.stdin.read (1)

    def refresh (self, blocks):
        """Print the blocks with height and width left on the screen."""
        os.system ('clear')
        leftHeight = self.__height
        for block in blocks:
            if leftHeight <= 2:
                """Do not show the block if there are not left lines for header and a row."""
                break
            height = len (block) if len (block) < leftHeight else leftHeight
            block.printLines (height, self.__width)
            leftHeight -= height
            if leftHeight >= 2:
                print ()
                leftHeight -= 1

    def askForInput (self, *attributes):
        """Ask for input for given attributes in given order."""
        with self.__consoleDeactivator:
            print ()
            values = []
            for attribute in attributes:
                value = input (attribute + ': ')
                if not value:
                    break
                values.append (value)
        return values

class Configuration:
    defaultFile = os.path.splitext (__file__) [0] + '.conf'
    optionalVariables = ['username', 'password']
    choices = ['status', 'replicationInfo', 'replicaSet', 'operations', 'replicationOperations']

    def __init__ (self, filePath):
        """Parse the configuration file using the ConfigParser class from default Python library. Two attempts to
        import the same class for Python 3 compatibility."""
        defaults = [(variable, None) for variable in self.optionalVariables]
        defaults += [(choice, 'on') for choice in self.choices]
        try:
            from ConfigParser import SafeConfigParser
        except ImportError:
            from configparser import SafeConfigParser
        self.__parser = SafeConfigParser (dict (defaults))
        self.__parser.read (filePath)

    def sections (self):
        if self.__parser:
            return self.__parser.sections ()

    def __server (self, section):
        address = self.__parser.get (section, 'address')
        username = self.__parser.get (section, 'username')
        password = self.__parser.get (section, 'password')
        return Server (section, address, username, password)

    def chosenServers (self, choice):
        return [self.__server (section) for section in self.sections () if self.__parser.getboolean (section, choice)]

class QueryScreen:
    def __init__ (self, console, **chosenServers):
        self.__console = console
        self.__chosenServers = chosenServers

    def __status (self):
        chosenServers = self.__chosenServers ['status']
        return (server.status () for server in chosenServers)
 
    def __replicationInfos (self):
        replicationInfos = []
        chosenServers = self.__chosenServers ['replicationInfo']
        for server in chosenServers:
            replicationInfo = server.replicationInfo ()
            if replicationInfo:
                replicationInfos.append (replicationInfo)
            else:
                chosenServers.remove (server)
        return replicationInfos

    def __replicaSetMembers (self):
        """Return unique replica sets of the servers."""
        replicaSets = []
        chosenServers = self.__chosenServers ['replicaSet']
        def add (replicaSet):
            """Merge same replica sets by revising the existent one."""
            for existentReplicaSet in replicaSets:
                if str (existentReplicaSet) == str (replicaSet):
                    return existentReplicaSet.revise (replicaSet)
            return replicaSets.append (replicaSet)
        for server in chosenServers:
            try:
                add (server.replicaSet ())
            except ExecuteFailure:
                chosenServers.remove (server)
        return [member for replicaSet in replicaSets for member in replicaSet.members ()]

    def __operations (self):
        operations = []
        chosenServers = self.__chosenServers ['operations']
        for server in chosenServers:
            hideReplicationOperations = server not in self.__chosenServers ['replicationOperations']
            for operation in server.currentOperations (hideReplicationOperations):
                operations.append (operation)
        return sorted (operations, key = lambda operation: operation.sortOrder (), reverse = True)

    def __refresh (self):
        blocks = []
        if self.__chosenServers ['status']:
            blocks.append (ServerStatus.block)
            ServerStatus.block.reset (self.__status ())
        if self.__chosenServers ['replicationInfo']:
            blocks.append (ReplicationInfo.block)
            ReplicationInfo.block.reset (self.__replicationInfos ())
        if self.__chosenServers ['replicaSet']:
            blocks.append (ReplicaSetMember.block)
            ReplicaSetMember.block.reset (self.__replicaSetMembers ())
        if self.__chosenServers ['operations']:
            blocks.append (Operation.block)
            Operation.block.reset (self.__operations ())
        self.__console.refresh (blocks)

    def __askForOperation (self):
        operationInput = self.__console.askForInput ('Server', 'Opid')
        if len (operationInput) == 2:
            condition = lambda line: str (line [0]) == operationInput [0] and str (line [1]) == operationInput [1]
            operations = Operation.block.findLines (condition)
            if len (operations) == 1:
                return operations [0]

    def __explainAction (self):
        operation = self.__askForOperation ()
        if operation:
            if operation.exacutable ():
                operation.explain ()
            else:
                print ('Only queries with namespace can be explained.')

    def __killAction (self):
        operation = self.__askForOperation ()
        if operation:
            operation.kill ()

    def __batchKillAction (self):
        durationInput = self.__console.askForInput ('Sec')
        if durationInput:
            condition = lambda line: len (line) >= 3 and line [3] > int (durationInput [0])
            operations = Operation.block.findLines (condition)
            for operation in operations:
                operation.kill ()

    def action (self):
        """Refresh the screen, perform actions for the pressed button."""
        button = None
        while button != 'q':
            self.__refresh ()
            button = self.__console.checkButton (1)
            while button in ('e', 'k'):
                if button == 'e':
                    self.__explainAction ()
                elif button == 'k':
                    self.__killAction ()
                button = self.__console.checkButton ()
            if button == 'K':
                self.__batchKillAction ()

class Motop:
    """Realtime monitoring tool for several MongoDB servers. Shows current operations ordered by durations every
    second."""
    version = 1.0
    versionName = 'Motop ' + str (version)

    def parseArguments (self):
        """Create ArgumentParser instance. Return parsed arguments."""
        from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
        parser = ArgumentParser (formatter_class = ArgumentDefaultsHelpFormatter, description = self.__doc__)
        parser.add_argument ('addresses', metavar = 'address', nargs = '*', default = 'localhost:27017')
        parser.add_argument ('-c', '--conf', dest = 'conf', default = Configuration.defaultFile)
        parser.add_argument ('-V', '--version', action = 'version', version = self.versionName)
        return parser.parse_args ()

    def __init__ (self):
        """Parse arguments and the configuration file. Activate console. Get servers from the configuration file or
        from arguments. Show the query screen."""
        arguments = self.parseArguments ()
        configuration = Configuration (arguments.conf)
        with ConsoleActivator () as console:
            chosenServersForChoice = {}
            for choice in configuration.choices:
                if configuration.sections ():
                    chosenServersForChoice [choice] = configuration.chosenServers (choice)
                else:
                    chosenServersForChoice [choice] = [Server (address) for address in arguments.addresses]
            queryScreen = QueryScreen (console, **chosenServersForChoice)
            try:
                queryScreen.action ()
            except KeyboardInterrupt: pass

if __name__ == '__main__':
    """Run the main program."""
    Motop ()

