#!/usr/bin/env python
##
# Tart Database Operations
# "Top" Clone for MongoDB
#
# @author  Emre Hasegeli <emre.hasegeli@tart.com.tr>
# @date    2012-05-19
##

import sys
import os
import tty
import termios
import select
import json
from bson import json_util

class Operation:
    def __init__ (self, server, opid):
        self.__server = server
        self.__opid = opid

    def __eq__ (self, other):
        return True if str (self.__server) == str (other.__server) and self.__opid == other.__opid else False

    def getServer (self):
        return self.__server

    def sortOrder (self):
        return -1 * self.__opid

    def printLine (self):
        print str.ljust (str (self.__server), 14),
        print str.ljust (str (self.__opid), 10),

    def kill (self):
        return self.__server.killOperation (self.__opid)

class Query (Operation):
    def __init__ (self, server, opid, namespace, body, duration = None):
        Operation.__init__ (self, server, opid)
        self.__namespace = namespace
        self.__body = body
        self.__duration = duration

    def printLine (self):
        Operation.printLine (self)
        print str.ljust (str (self.__namespace), 18),
        print str.ljust (str (self.__duration), 4),
        print json.dumps (self.__body, default = json_util.default) [:80],

    def sortOrder (self):
        return self.__duration if self.__duration else 0

    def printExplain (self):
        if self.__namespace:
            server = self.getServer ()
            databaseName, collectionName = self.__namespace.split ('.', 1)
            explainOutput = server.explainQuery (databaseName, collectionName, self.__body)
            print 'Cursor:', explainOutput ['cursor']
            print 'Indexes:',
            for index in explainOutput ['indexBounds']:
                print index,
            print
            print 'IndexOnly:', explainOutput ['indexOnly']
            print 'MultiKey:', explainOutput ['isMultiKey']
            print 'Miliseconds:', explainOutput ['millis']
            print 'Documents:', explainOutput ['n']
            print 'ChunkSkips:', explainOutput ['nChunkSkips']
            print 'Yields:', explainOutput ['nYields']
            print 'Scanned:', explainOutput ['nscanned']
            print 'ScannedObjects:', explainOutput ['nscannedObjects']
            if explainOutput.has_key ('scanAndOrder'):
                print 'ScanAndOrder:', explainOutput ['scanAndOrder']
            print 'Query:', json.dumps (self.__body, default = json_util.default, sort_keys = True, indent = 4)
            return True
        return False

class Server:
    def __init__ (self, name, address):
        from pymongo import Connection
        assert len (name) < 14
        self.__name = name
        self.__address = address
        self.__connection = Connection (address)

    def printLine (self):
        serverStatus = self.__connection.admin.command ('serverStatus')
        print str.ljust (self.__name, 14),
        print str.ljust (str (serverStatus ['connections'] ['current']), 4) + '/',
        print str.ljust (str (serverStatus ['connections'] ['available']), 6),
        print str.ljust (str (serverStatus ['mem'] ['resident']), 6) + '/',
        print str.ljust (str (serverStatus ['mem'] ['mapped']), 8),
        return

    def explainQuery (self, databaseName, collectionName, query):
        database = getattr (self.__connection, databaseName)
        collection = getattr (database, collectionName)
        cursor = collection.find (query)
        return cursor.explain ()

    def currentOperations (self):
        for op in self.__connection.admin.current_op () ['inprog']:
            if op ['op'] == 'query':
                if op.has_key ('secs_running'):
                    yield Query (self, op ['opid'], op ['ns'], op ['query'], op ['secs_running'])
                else:
                    yield Query (self, op ['opid'], op ['ns'], op ['query'])
            else:
                yield Operation (self, op ['opid'])

    def killOperation (self, opid):
        os.system ('echo "db.killOp (' + str (opid) + ')" | mongo ' + self.__address)

    def __str__ (self):
        return self.__name

servers = {Server ('MongoDBMaster', '10.42.2.207'),
           Server ('MongoDB01' , '10.42.2.121'),
           Server ('MongoDB02', '10.42.2.122'),
           Server ('MongoDB03', '10.42.2.123'),
           Server ('DBAlpha', '10.42.2.206')}

class Frame:
    def __init__ (self, operations):
        self.__operations = sorted (operations, key = lambda operation: operation.sortOrder (), reverse = True)
        os.system ('clear')
        print 'Server         Connections  Memory'
        for server in servers:
            server.printLine ()
            print
        print
        print 'Server         OpId       Namespace          Sec  Query'
        for operation in self.__operations:
            operation.printLine ()
            print

    def findCurrentOperation (self, operation):
        for currentOperation in self.__operations:
            if currentOperation == operation:
                return currentOperation

class ConsoleActivator ():
    def __enter__ (self, *args):
        self.__settings = termios.tcgetattr (sys.stdin)
        tty.setcbreak (sys.stdin.fileno())
        return Console (self)

    def __exit__ (self, *args):
        termios.tcsetattr (sys.stdin, termios.TCSADRAIN, self.__settings)

class ConsoleDeactivator ():
    def __init__ (self, consoleActivator):
        self.__enter__ = consoleActivator.__exit__
        self.__exit__ = consoleActivator.__enter__

class Console:
    def __init__ (self, consoleActivator):
        self.__consoleDeactivator = ConsoleDeactivator (consoleActivator)

    def getInput (self):
        input = sys.stdin.read (1)
        if input in ('e', 'k', 'q'):
            return input

    def checkInput (self):
        if select.select ([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
            return self.getInput ()

    def askForOperation (self):
        with self.__consoleDeactivator:
            print
            serverName = raw_input ('Server: ',)
            if serverName:
                opid = int (raw_input ('OpId: ',))
                if opid:
                    return Operation (serverName, opid)

if __name__ == '__main__':
    from time import sleep
    input = None
    with ConsoleActivator () as console:
        while input != 'q':
            if not input:
                frame = Frame ([operation for server in servers for operation in server.currentOperations ()])
                sleep (1)
                input = console.checkInput ()
            if input in ('e', 'k'):
                operation = console.askForOperation ()
                if operation:
                    currentOperation = frame.findCurrentOperation (operation)
                    if currentOperation:
                        if input == 'e':
                            if isinstance (currentOperation, Query):
                                currentOperation.printExplain ()
                            else:
                                print 'Only queries with namespace can be explained.'
                        elif input == 'k':
                            currentOperation.kill ()
                    else:
                        print 'Invalid operation.'
                    input = console.getInput ()
                else:
                    input = None
