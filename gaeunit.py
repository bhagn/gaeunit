#!/usr/bin/env python
'''
GAEUnit: Google App Engine Unit Test Framework

Usage:

1. Put gaeunit.py into your application directory.  Modify 'app.yaml' by
   adding the following mapping below the 'handlers:' section:

   - url: /test.*
     script: gaeunit.py

2. Write your own test cases by extending unittest.TestCase.

3. Launch the development web server.  To run all tests, point your browser to:

   http://localhost:8080/test     (Modify the port if necessary.)
   
   For plain text output add '?format=plain' to the above URL.
   See README.TXT for information on how to run specific tests.

4. The results are displayed as the tests are run.

Visit http://code.google.com/p/gaeunit for more information and updates.

------------------------------------------------------------------------------
Copyright (c) 2008-2009, George Lei and Steven R. Farley.  All rights reserved.

Distributed under the following BSD license:

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
------------------------------------------------------------------------------
'''

__author__ = "George Lei and Steven R. Farley"
__email__ = "George.Z.Lei@Gmail.com"
__version__ = "#Revision: 1.2.8 $"[11:-2]
__copyright__= "Copyright (c) 2008-2009, George Lei and Steven R. Farley"
__license__ = "BSD"
__url__ = "http://code.google.com/p/gaeunit"

import sys
import os
import unittest
import time
import logging
import cgi
import django.utils.simplejson
from django.template import Context, Template

from google.appengine.ext import webapp
from google.appengine.api import apiproxy_stub_map  
from google.appengine.api import datastore_file_stub
from google.appengine.ext.webapp.util import run_wsgi_app

from django.conf import settings

try:
    settings.configure(DEBUG=True, TEMPLATE_DEBUG=True, TEMPLATE_DIRS=())
except Exception:
    pass

_LOCAL_TEST_DIR = 'test'  # location of files
_WEB_TEST_DIR = '/test'   # how you want to refer to tests on your web server

# or:
# _WEB_TEST_DIR = '/u/test'
# then in app.yaml:
#   - url: /u/test.*
#     script: gaeunit.py


##############################################################################
# Main request handler
##############################################################################


class MainTestPageHandler(webapp.RequestHandler):
    def get(self):
        unknown_args = [arg for arg in self.request.arguments()
                        if arg not in ("format", "package", "name")]
        if len(unknown_args) > 0:
            errors = []
            for arg in unknown_args:
                errors.append(_log_error("The request parameter '%s' is not valid." % arg))
            self.error(404)
            self.response.out.write(" ".join(errors))
            return

        format = self.request.get("format", "html")
        if format == "html":
            self._render_html()
        elif format == "plain":
            self._render_plain()
        else:
            error = _log_error("The format '%s' is not valid." % cgi.escape(format))
            self.error(404)
            self.response.out.write(error)
            
    def _render_html(self):
        suite, error = _create_suite(self.request)
        if not error:
            d = {}
            d["suite"] = _test_suite_to_json(suite)
            d["dir"] = _WEB_TEST_DIR
            t = Template(_MAIN_PAGE_CONTENT)
            self.response.out.write(t.render(Context(d)))
            #self.response.out.write(_MAIN_PAGE_CONTENT % (_test_suite_to_json(suite), _WEB_TEST_DIR, __version__))
        else:
            self.error(404)
            self.response.out.write(error)
        
    def _render_plain(self):
        self.response.headers["Content-Type"] = "text/plain"
        runner = unittest.TextTestRunner(self.response.out)
        suite, error = _create_suite(self.request)
        if not error:
            self.response.out.write("====================\n" \
                                    "GAEUnit Test Results\n" \
                                    "====================\n\n")
            _run_test_suite(runner, suite)
        else:
            self.error(404)
            self.response.out.write(error)


##############################################################################
# JSON test classes
##############################################################################


class JsonTestResult(unittest.TestResult):
    def __init__(self):
        unittest.TestResult.__init__(self)
        self.testNumber = 0

    def render_to(self, stream):
        result = {
            'runs': self.testsRun,
            'total': self.testNumber,
            'errors': self._list(self.errors),
            'failures': self._list(self.failures),
            }

        stream.write(django.utils.simplejson.dumps(result).replace('},', '},\n'))

    def _list(self, list):
        dict = []
        for test, err in list:
            d = { 
              'desc': test.shortDescription() or str(test), 
              'detail': err,
            }
            dict.append(d)
        return dict


class JsonTestRunner:
    def run(self, test):
        self.result = JsonTestResult()
        self.result.testNumber = test.countTestCases()
        startTime = time.time()
        test(self.result)
        stopTime = time.time()
        timeTaken = stopTime - startTime
        return self.result


class ArivuTestResult(unittest.TestResult):
    def __init__(self, runner):
        super(ArivuTestResult, self).__init__()
        self.runner = runner
        self.result = {}

    def startTest(self, test):
        super(ArivuTestResult, self).startTest(test)

        test_class = test.__str__().split("(")[1].split(")")[0]
        self.runner.result[test._testMethodName] = {}
        self.runner.result[test._testMethodName]["test"] = test_class
        self.runner.result[test._testMethodName]["method"] = test._testMethodName
        self.runner.result[test._testMethodName]["status"] = None
        self.runner.result[test._testMethodName]["description"] = test.shortDescription()

    def addSuccess(self, test):
        super(ArivuTestResult, self).addSuccess(test)
        self.runner.result[test._testMethodName]["status"] = "Passed"

    def addFailure(self, test, err):
        super(ArivuTestResult, self).addFailure(test, err)
        self.runner.result[test._testMethodName]["status"] = "Failed"
        self.runner.result[test._testMethodName]["traceback"] = self.failures[-1][1]

    def addError(self, test, err):
        super(ArivuTestResult, self).addError(test, err)
        self.runner.result[test._testMethodName]["status"] = "Error"
        self.runner.result[test._testMethodName]["traceback"] = self.errors[-1][1]



class ArivuTestRunner:
    def __init__(self, stream):
        self.result = {}

    def run(self, test):
        result = ArivuTestResult(self)
        test(result)
        return result

class JsonTestRunHandler(webapp.RequestHandler):
    def get(self):
        self.response.headers["Content-Type"] = "text/html"
        test_name = self.request.get("name")
        _load_default_test_modules()
        suite = unittest.defaultTestLoader.loadTestsFromName(test_name)
        runner = ArivuTestRunner(self.response.out)
        _run_test_suite(runner, suite)
        t = Template(TEST_RUNS_PAGE)
        d = {}
        d["results"] = runner.result
        d["css"] = PURE_CSS
        if self.request.get("format", "html") == "json":
            self.response.headers["Content-Type"] = "application/json"
            self.response.out.write(django.utils.simplejson.dumps(runner.result))
        else:
            self.response.out.write(t.render(Context(d)))


# This is not used by the HTML page, but it may be useful for other client test runners.
class JsonTestListHandler(webapp.RequestHandler):
    def get(self):
        self.response.headers["Content-Type"] = "text/javascript"
        suite, error = _create_suite(self.request)
        if not error:
            self.response.out.write(_test_suite_to_json(suite))
        else:
            self.error(404)
            self.response.out.write(error)

class CSSHandler(webapp.RequestHandler):
    def get(self):
        self.response.headers["Content-Type"] = "text/css"
        self.response.out.write(PURE_CSS)

##############################################################################
# Module helper functions
##############################################################################


def _create_suite(request):
    package_name = request.get("package")
    test_name = request.get("name")

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()

    error = None

    try:
        if not package_name and not test_name:
                modules = _load_default_test_modules()
                for module in modules:
                    suite.addTest(loader.loadTestsFromModule(module))
        elif test_name:
                _load_default_test_modules()
                suite.addTest(loader.loadTestsFromName(test_name))
        elif package_name:
                package = reload(__import__(package_name))
                module_names = package.__all__
                for module_name in module_names:
                    suite.addTest(loader.loadTestsFromName('%s.%s' % (package_name, module_name)))
    
        if suite.countTestCases() == 0:
            raise Exception("'%s' is not found or does not contain any tests." %  \
                            (test_name or package_name or 'local directory: \"%s\"' % _LOCAL_TEST_DIR))
    except Exception, e:
        error = str(e)
        _log_error(error)

    return (suite, error)


def _load_default_test_modules():
    if not _LOCAL_TEST_DIR in sys.path:
        sys.path.append(_LOCAL_TEST_DIR)
    module_names = [mf[0:-3] for mf in os.listdir(_LOCAL_TEST_DIR) if mf.endswith(".py")]
    return [reload(__import__(name)) for name in module_names]


def _get_tests_from_suite(suite, tests):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            _get_tests_from_suite(test, tests)
        else:
            tests.append(test)


def _test_suite_to_json(suite):
    tests = []
    _get_tests_from_suite(suite, tests)
    test_tuples = [(type(test).__module__, type(test).__name__, test._testMethodName) \
                   for test in tests]
    test_dict = {}
    for test_tuple in test_tuples:
        module_name, class_name, method_name = test_tuple
        if module_name not in test_dict:
            mod_dict = {}
            method_list = []
            method_list.append(method_name)
            mod_dict[class_name] = method_list
            test_dict[module_name] = mod_dict
        else:
            mod_dict = test_dict[module_name]
            if class_name not in mod_dict:
                method_list = []
                method_list.append(method_name)
                mod_dict[class_name] = method_list
            else:
                method_list = mod_dict[class_name]
                method_list.append(method_name)
                
    return django.utils.simplejson.dumps(test_dict)


def _run_test_suite(runner, suite):
    """Run the test suite.

    Preserve the current development apiproxy, create a new apiproxy and
    replace the datastore with a temporary one that will be used for this
    test suite, run the test suite, and restore the development apiproxy.
    This isolates the test datastore from the development datastore.

    """        
    original_apiproxy = apiproxy_stub_map.apiproxy
    try:
       apiproxy_stub_map.apiproxy = apiproxy_stub_map.APIProxyStubMap() 
       temp_stub = datastore_file_stub.DatastoreFileStub('GAEUnitDataStore', None, None, trusted=True)  
       apiproxy_stub_map.apiproxy.RegisterStub('datastore', temp_stub)
       # Allow the other services to be used as-is for tests.
       for name in ['user', 'urlfetch', 'mail', 'memcache', 'images', 'logservice']:
           apiproxy_stub_map.apiproxy.RegisterStub(name, original_apiproxy.GetStub(name))
       runner.run(suite)
    finally:
       apiproxy_stub_map.apiproxy = original_apiproxy


def _log_error(s):
   logging.warn(s)
   return s

           
################################################
# Browser HTML, CSS, and Javascript
################################################


# This string uses Python string formatting, so be sure to escape percents as %%.
_MAIN_PAGE_CONTENT = """
<html>
<head>
    <link rel="stylesheet" href="/test/css">
    <style>
        body {font-family:arial,sans-serif; #text-align:center}
        #title {font-family:"Times New Roman","Times Roman",TimesNR,times,serif; font-size:28px; font-weight:bold; text-align:center}
        #version {font-size:87%%; text-align:center;}
        #weblink {font-style:italic; text-align:center; padding-top:7px; padding-bottom:7px}
        #results {padding-top:20px; margin:0pt auto; text-align:center; font-weight:bold}
        #testindicator {width:750px; height:16px; border: 1px solid #18BC9C; background-color:#18BC9C;}
        #footerarea {text-align:center; font-size:83%%; padding-top:25px}
        #errorarea {padding-top:25px}
        .error {border-color: #c3d9ff; border-style: solid; border-width: 2px 1px 2px 1px; width:750px; padding:1px; margin:0pt auto; text-align:left}
        .errtitle {background-color:#c3d9ff; font-weight:bold}
        .indicator {border: 1px solid #18BC9C; margin: 10px;}
        .done {width: 0px; height: 20px; background: #18BC9C; transition: all ease-out 0.1s;}
        pre {font-size: 11px;}
    </style>

    <script language="javascript" type="text/javascript">
        var testsToRun = {{suite | safe}};
        var totalTests = 0;
        var totalRuns = 0;
        var totalErrors = 0;
        var totalFailures = 0;

        function newXmlHttp() {
          try { return new XMLHttpRequest(); } catch(e) {}
          try { return new ActiveXObject("Msxml2.XMLHTTP"); } catch (e) {}
          try { return new ActiveXObject("Microsoft.XMLHTTP"); } catch (e) {}
          alert("XMLHttpRequest not supported");
          return null;
        }

        function addTestToMenu(test) {
            var li = document.createElement('li');
            li.style.borderBottom = '1px solid #eee';

            var a = document.createElement('a');
            a.href = '#' + test.test + test.method;

            var small = document.createElement('small');
            small.innerHTML = test.test + ' <span style="color: red;">&#x26a0;</span><br/>';

            var span = document.createElement('span');
            span.innerHTML = test.method;

            a.appendChild(small);
            a.appendChild(span);

            li.appendChild(a);

            document.getElementById('menu').appendChild(li);


            var div = document.createElement('div');
            div.style.margin = '10px';
            div.innerHTML = '<h1 style="font-weight: 100;" id="' + test.test + test.method + '">' + test.method + '</h1>';
            div.innerHTML += '<a href="{{dir}}/run?name=' + test.test + '.' + test.method + '" style="float:right;" class="pure-button button-secondary">Run test</a>'
            div.innerHTML += '<blockquote>' + test.description + '</blockquote>';
            div.innerHTML += '<div>' + test.status + '</div>';
            div.innerHTML += '<pre>' + test.traceback + '</pre>';

            document.getElementById('content').appendChild(div);

        }
        
        function requestTestRun(moduleName, className, methodName) {
            var methodSuffix = "";
            if (methodName) {
                methodSuffix = "." + methodName;
            }
            var xmlHttp = newXmlHttp();
            xmlHttp.open("GET", "{{dir}}/run?format=json&name=" + moduleName + "." + className + methodSuffix, true);
            xmlHttp.onreadystatechange = function() {
                if (xmlHttp.readyState != 4) {
                    return;
                }
                if (xmlHttp.status == 200) {
                    var result = JSON.parse(xmlHttp.responseText);
                    var test = result[Object.keys(result)[0]];

                    totalRuns ++;
                    if(test.status == "Error") {
                        totalErrors++;
                        addTestToMenu(test);
                    }
                    if(test.status == "Failed") {
                        totalFailures++;
                        addTestToMenu(test);
                    }
                    document.getElementById("done").style.width = parseInt(totalRuns * 100/totalTests) + '%';
                    document.getElementById("testran").innerHTML = totalRuns;
                    document.getElementById("testerror").innerHTML = totalErrors;
                    document.getElementById("testfailure").innerHTML = totalFailures;
                } else {
                    document.getElementById("content").innerHTML = xmlHttp.responseText;
                    document.getElementById("content").style.color = 'red';
                    testFailed();
                }
            };
            xmlHttp.send(null);            
        }

        function testFailed() {
            document.getElementById("testindicator").style.backgroundColor="red";
        }
        
        function testSucceed() {
            document.getElementById("testindicator").style.backgroundColor="green";
        }
        
        function runTests() {
            // Run each test asynchronously (concurrently).
            totalTests = 0;
            for (var moduleName in testsToRun) {
                var classes = testsToRun[moduleName];
                for (var className in classes) {
                    // TODO: Optimize for the case where tests are run by class so we don't
                    //       have to always execute each method separately.  This should be
                    //       possible when we have a UI that allows the user to select tests
                    //       by module, class, and method.
                    //requestTestRun(moduleName, className);
                    methods = classes[className];
                    for (var i = 0; i < methods.length; i++) {
                        totalTests += 1;
                        var methodName = methods[i];
                        requestTestRun(moduleName, className, methodName);
                    }
                }
            }
            document.getElementById("testtotal").innerHTML = totalTests;
        }

    </script>
    <title>GAEUnit: Google App Engine Unit Test Framework</title>
</head>
<body onload="runTests()">

    <div class="pure-g" style="padding: 10px;">
        <div class="pure-u-1">
            <div style="margin: 5px; border: 1px solid #eee;padding: 5px;">
                <span style="display: inline-block; width: 120px;"><small>Total Tests: </small><span id="testtotal"></span></span>
                <span style="display: inline-block; width: 120px;"><small>[&#x2713;]Ran: </small><span id="testran"></span></span>
                <span style="display: inline-block; width: 120px;"><small>[&times;]Errors: </small><span id="testerror"></span></span>
                <span style="display: inline-block; width: 120px;"><small>[&#x26a0;]Failures: </small><span id="testfailure"></span></span>
            </div>
        </div>
    </div>
    <div class="pure-g">
        <div class="pure-u-1-4">
            <div style="margin: 10px;">
                <div class="indicator">
                    <div id="done" class="done"></div>
                </div>

                <div class="pure-menu pure-menu-open">
                    <ul id="menu">
                    </ul>
                </div>
            </div>
        </div>
        <div class="pure-u-3-4">
            <div style="margin: 10px;" id="content"></div>
        </div>
    </div>

</body>
</html>
"""

TEST_RUNS_PAGE = """
<!DOCTYPE html>
<html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Unit Test Results</title>
      <link rel="stylesheet" href="/test/css">
      <style type="text/css">
        pre {font-size: 11px;}
      </style>
    </head>
    <body>
      <div class="pure-g">
        <div class="pure-u-1-4">
          <div class="pure-menu pure-menu-open" style="margin: 20px;">
            <a class="pure-menu-heading">Test Cases Run</a>
            <ul>
            {% for v in results.itervalues %}
              <li style="border-bottom: 1px solid #eee;"><a href="#{{v.test}}{{v.method}}">
                <small>{{v.test}}</small>
                {% if v.status != "Passed" %}
                  <span style="color: red;">&#x26a0;</span>
                {% endif %}
                <br/>
                {{v.method}}
              </a></li>
            {% endfor %}
            </ul>
          </div>
        </div>
        <div class="pure-u-3-4">
          <div style="margin: 50px;">
          {% for v in results.itervalues %}
          {% if v.status != "Passed" %}
            <h1 style="font-weight: 100;" id="{{v.test}}{{v.method}}">{{v.method}}</h1>
            <a href="/test/run?name={{v.test}}.{{v.method}}" class="pure-button" style="float: right;">Run Test</a>
            <blockquote>{{v.description}}</blockquote>
            <div>{{v.status}}</div>
            {% if v.traceback %}
              <pre>{{v.traceback}}</pre>
            {% endif %}
          {% endif %}
          {% endfor %}
          </div>
        </div>
      </div>

    </body>
</html>
"""

PURE_CSS = """
article,aside,details,figcaption,figure,footer,header,hgroup,main,nav,section,summary{display:block}audio,canvas,video{display:inline-block;*display:inline;*zoom:1}audio:not([controls]){display:none;height:0}[hidden]{display:none}html{font-size:100%;-ms-text-size-adjust:100%;-webkit-text-size-adjust:100%}html,button,input,select,textarea{font-family:sans-serif}body{margin:0}a:focus{outline:thin dotted}a:active,a:hover{outline:0}h1{font-size:2em;margin:.67em 0}h2{font-size:1.5em;margin:.83em 0}h3{font-size:1.17em;margin:1em 0}h4{font-size:1em;margin:1.33em 0}h5{font-size:.83em;margin:1.67em 0}h6{font-size:.67em;margin:2.33em 0}abbr[title]{border-bottom:1px dotted}b,strong{font-weight:700}blockquote{margin:1em 40px}dfn{font-style:italic}hr{-moz-box-sizing:content-box;box-sizing:content-box;height:0}mark{background:#ff0;color:#000}p,pre{margin:1em 0}code,kbd,pre,samp{font-family:monospace,serif;_font-family:'courier new',monospace;font-size:1em}pre{white-space:pre;white-space:pre-wrap;word-wrap:break-word}q{quotes:none}q:before,q:after{content:'';content:none}small{font-size:80%}sub,sup{font-size:75%;line-height:0;position:relative;vertical-align:baseline}sup{top:-.5em}sub{bottom:-.25em}dl,menu,ol,ul{margin:1em 0}dd{margin:0 0 0 40px}menu,ol,ul{padding:0 0 0 40px}nav ul,nav ol{list-style:none;list-style-image:none}img{border:0;-ms-interpolation-mode:bicubic}svg:not(:root){overflow:hidden}figure{margin:0}form{margin:0}fieldset{border:1px solid silver;margin:0 2px;padding:.35em .625em .75em}legend{border:0;padding:0;white-space:normal;*margin-left:-7px}button,input,select,textarea{font-size:100%;margin:0;vertical-align:baseline;*vertical-align:middle}button,input{line-height:normal}button,select{text-transform:none}button,html input[type=button],input[type=reset],input[type=submit]{-webkit-appearance:button;cursor:pointer;*overflow:visible}button[disabled],html input[disabled]{cursor:default}input[type=checkbox],input[type=radio]{box-sizing:border-box;padding:0;*height:13px;*width:13px}input[type=search]{-webkit-appearance:textfield;-moz-box-sizing:content-box;-webkit-box-sizing:content-box;box-sizing:content-box}input[type=search]::-webkit-search-cancel-button,input[type=search]::-webkit-search-decoration{-webkit-appearance:none}button::-moz-focus-inner,input::-moz-focus-inner{border:0;padding:0}textarea{overflow:auto;vertical-align:top}table{border-collapse:collapse;border-spacing:0}[hidden]{display:none!important}.pure-img{max-width:100%;height:auto;display:block}.pure-g{letter-spacing:-.31em;*letter-spacing:normal;*word-spacing:-.43em;text-rendering:optimizespeed;font-family:FreeSans,Arimo,"Droid Sans",Helvetica,Arial,sans-serif;display:-webkit-flex;-webkit-flex-flow:row wrap;display:-ms-flexbox;-ms-flex-flow:row wrap}.opera-only :-o-prefocus,.pure-g{word-spacing:-.43em}.pure-u{display:inline-block;*display:inline;zoom:1;letter-spacing:normal;word-spacing:normal;vertical-align:top;text-rendering:auto}.pure-g [class *="pure-u"]{font-family:sans-serif}.pure-u-1,.pure-u-1-1,.pure-u-1-2,.pure-u-1-3,.pure-u-2-3,.pure-u-1-4,.pure-u-3-4,.pure-u-1-5,.pure-u-2-5,.pure-u-3-5,.pure-u-4-5,.pure-u-5-5,.pure-u-1-6,.pure-u-5-6,.pure-u-1-8,.pure-u-3-8,.pure-u-5-8,.pure-u-7-8,.pure-u-1-12,.pure-u-5-12,.pure-u-7-12,.pure-u-11-12,.pure-u-1-24,.pure-u-2-24,.pure-u-3-24,.pure-u-4-24,.pure-u-5-24,.pure-u-6-24,.pure-u-7-24,.pure-u-8-24,.pure-u-9-24,.pure-u-10-24,.pure-u-11-24,.pure-u-12-24,.pure-u-13-24,.pure-u-14-24,.pure-u-15-24,.pure-u-16-24,.pure-u-17-24,.pure-u-18-24,.pure-u-19-24,.pure-u-20-24,.pure-u-21-24,.pure-u-22-24,.pure-u-23-24,.pure-u-24-24{display:inline-block;*display:inline;zoom:1;letter-spacing:normal;word-spacing:normal;vertical-align:top;text-rendering:auto}.pure-u-1-24{width:4.1667%;*width:4.1357%}.pure-u-1-12,.pure-u-2-24{width:8.3333%;*width:8.3023%}.pure-u-1-8,.pure-u-3-24{width:12.5%;*width:12.469%}.pure-u-1-6,.pure-u-4-24{width:16.6667%;*width:16.6357%}.pure-u-1-5{width:20%;*width:19.969%}.pure-u-5-24{width:20.8333%;*width:20.8023%}.pure-u-1-4,.pure-u-6-24{width:25%;*width:24.969%}.pure-u-7-24{width:29.1667%;*width:29.1357%}.pure-u-1-3,.pure-u-8-24{width:33.3333%;*width:33.3023%}.pure-u-3-8,.pure-u-9-24{width:37.5%;*width:37.469%}.pure-u-2-5{width:40%;*width:39.969%}.pure-u-5-12,.pure-u-10-24{width:41.6667%;*width:41.6357%}.pure-u-11-24{width:45.8333%;*width:45.8023%}.pure-u-1-2,.pure-u-12-24{width:50%;*width:49.969%}.pure-u-13-24{width:54.1667%;*width:54.1357%}.pure-u-7-12,.pure-u-14-24{width:58.3333%;*width:58.3023%}.pure-u-3-5{width:60%;*width:59.969%}.pure-u-5-8,.pure-u-15-24{width:62.5%;*width:62.469%}.pure-u-2-3,.pure-u-16-24{width:66.6667%;*width:66.6357%}.pure-u-17-24{width:70.8333%;*width:70.8023%}.pure-u-3-4,.pure-u-18-24{width:75%;*width:74.969%}.pure-u-19-24{width:79.1667%;*width:79.1357%}.pure-u-4-5{width:80%;*width:79.969%}.pure-u-5-6,.pure-u-20-24{width:83.3333%;*width:83.3023%}.pure-u-7-8,.pure-u-21-24{width:87.5%;*width:87.469%}.pure-u-11-12,.pure-u-22-24{width:91.6667%;*width:91.6357%}.pure-u-23-24{width:95.8333%;*width:95.8023%}.pure-u-1,.pure-u-1-1,.pure-u-5-5,.pure-u-24-24{width:100%}.pure-button{display:inline-block;*display:inline;zoom:1;line-height:normal;white-space:nowrap;vertical-align:baseline;text-align:center;cursor:pointer;-webkit-user-drag:none;-webkit-user-select:none;-moz-user-select:none;-ms-user-select:none;user-select:none}.pure-button::-moz-focus-inner{padding:0;border:0}.pure-button{font-family:inherit;font-size:100%;*font-size:90%;*overflow:visible;padding:.5em 1em;color:#444;color:rgba(0,0,0,.8);*color:#444;border:1px solid #999;border:0 rgba(0,0,0,0);background-color:#E6E6E6;text-decoration:none;border-radius:2px}.pure-button-hover,.pure-button:hover,.pure-button:focus{filter:progid:DXImageTransform.Microsoft.gradient(startColorstr='#00000000', endColorstr='#1a000000', GradientType=0);background-image:-webkit-gradient(linear,0 0,0 100%,from(transparent),color-stop(40%,rgba(0,0,0,.05)),to(rgba(0,0,0,.1)));background-image:-webkit-linear-gradient(transparent,rgba(0,0,0,.05) 40%,rgba(0,0,0,.1));background-image:-moz-linear-gradient(top,rgba(0,0,0,.05) 0,rgba(0,0,0,.1));background-image:-o-linear-gradient(transparent,rgba(0,0,0,.05) 40%,rgba(0,0,0,.1));background-image:linear-gradient(transparent,rgba(0,0,0,.05) 40%,rgba(0,0,0,.1))}.pure-button:focus{outline:0}.pure-button-active,.pure-button:active{box-shadow:0 0 0 1px rgba(0,0,0,.15) inset,0 0 6px rgba(0,0,0,.2) inset}.pure-button[disabled],.pure-button-disabled,.pure-button-disabled:hover,.pure-button-disabled:focus,.pure-button-disabled:active{border:0;background-image:none;filter:progid:DXImageTransform.Microsoft.gradient(enabled=false);filter:alpha(opacity=40);-khtml-opacity:.4;-moz-opacity:.4;opacity:.4;cursor:not-allowed;box-shadow:none}.pure-button-hidden{display:none}.pure-button::-moz-focus-inner{padding:0;border:0}.pure-button-primary,.pure-button-selected,a.pure-button-primary,a.pure-button-selected{background-color:#0078e7;color:#fff}.pure-form input[type=text],.pure-form input[type=password],.pure-form input[type=email],.pure-form input[type=url],.pure-form input[type=date],.pure-form input[type=month],.pure-form input[type=time],.pure-form input[type=datetime],.pure-form input[type=datetime-local],.pure-form input[type=week],.pure-form input[type=number],.pure-form input[type=search],.pure-form input[type=tel],.pure-form input[type=color],.pure-form select,.pure-form textarea{padding:.5em .6em;display:inline-block;border:1px solid #ccc;box-shadow:inset 0 1px 3px #ddd;border-radius:4px;-webkit-box-sizing:border-box;-moz-box-sizing:border-box;box-sizing:border-box}.pure-form input:not([type]){padding:.5em .6em;display:inline-block;border:1px solid #ccc;box-shadow:inset 0 1px 3px #ddd;border-radius:4px;-webkit-box-sizing:border-box;-moz-box-sizing:border-box;box-sizing:border-box}.pure-form input[type=color]{padding:.2em .5em}.pure-form input[type=text]:focus,.pure-form input[type=password]:focus,.pure-form input[type=email]:focus,.pure-form input[type=url]:focus,.pure-form input[type=date]:focus,.pure-form input[type=month]:focus,.pure-form input[type=time]:focus,.pure-form input[type=datetime]:focus,.pure-form input[type=datetime-local]:focus,.pure-form input[type=week]:focus,.pure-form input[type=number]:focus,.pure-form input[type=search]:focus,.pure-form input[type=tel]:focus,.pure-form input[type=color]:focus,.pure-form select:focus,.pure-form textarea:focus{outline:0;outline:thin dotted \9;border-color:#129FEA}.pure-form input:not([type]):focus{outline:0;outline:thin dotted \9;border-color:#129FEA}.pure-form input[type=file]:focus,.pure-form input[type=radio]:focus,.pure-form input[type=checkbox]:focus{outline:thin dotted #333;outline:1px auto #129FEA}.pure-form .pure-checkbox,.pure-form .pure-radio{margin:.5em 0;display:block}.pure-form input[type=text][disabled],.pure-form input[type=password][disabled],.pure-form input[type=email][disabled],.pure-form input[type=url][disabled],.pure-form input[type=date][disabled],.pure-form input[type=month][disabled],.pure-form input[type=time][disabled],.pure-form input[type=datetime][disabled],.pure-form input[type=datetime-local][disabled],.pure-form input[type=week][disabled],.pure-form input[type=number][disabled],.pure-form input[type=search][disabled],.pure-form input[type=tel][disabled],.pure-form input[type=color][disabled],.pure-form select[disabled],.pure-form textarea[disabled]{cursor:not-allowed;background-color:#eaeded;color:#cad2d3}.pure-form input:not([type])[disabled]{cursor:not-allowed;background-color:#eaeded;color:#cad2d3}.pure-form input[readonly],.pure-form select[readonly],.pure-form textarea[readonly]{background:#eee;color:#777;border-color:#ccc}.pure-form input:focus:invalid,.pure-form textarea:focus:invalid,.pure-form select:focus:invalid{color:#b94a48;border-color:#ee5f5b}.pure-form input:focus:invalid:focus,.pure-form textarea:focus:invalid:focus,.pure-form select:focus:invalid:focus{border-color:#e9322d}.pure-form input[type=file]:focus:invalid:focus,.pure-form input[type=radio]:focus:invalid:focus,.pure-form input[type=checkbox]:focus:invalid:focus{outline-color:#e9322d}.pure-form select{border:1px solid #ccc;background-color:#fff}.pure-form select[multiple]{height:auto}.pure-form label{margin:.5em 0 .2em}.pure-form fieldset{margin:0;padding:.35em 0 .75em;border:0}.pure-form legend{display:block;width:100%;padding:.3em 0;margin-bottom:.3em;color:#333;border-bottom:1px solid #e5e5e5}.pure-form-stacked input[type=text],.pure-form-stacked input[type=password],.pure-form-stacked input[type=email],.pure-form-stacked input[type=url],.pure-form-stacked input[type=date],.pure-form-stacked input[type=month],.pure-form-stacked input[type=time],.pure-form-stacked input[type=datetime],.pure-form-stacked input[type=datetime-local],.pure-form-stacked input[type=week],.pure-form-stacked input[type=number],.pure-form-stacked input[type=search],.pure-form-stacked input[type=tel],.pure-form-stacked input[type=color],.pure-form-stacked select,.pure-form-stacked label,.pure-form-stacked textarea{display:block;margin:.25em 0}.pure-form-stacked input:not([type]){display:block;margin:.25em 0}.pure-form-aligned input,.pure-form-aligned textarea,.pure-form-aligned select,.pure-form-aligned .pure-help-inline,.pure-form-message-inline{display:inline-block;*display:inline;*zoom:1;vertical-align:middle}.pure-form-aligned textarea{vertical-align:top}.pure-form-aligned .pure-control-group{margin-bottom:.5em}.pure-form-aligned .pure-control-group label{text-align:right;display:inline-block;vertical-align:middle;width:10em;margin:0 1em 0 0}.pure-form-aligned .pure-controls{margin:1.5em 0 0 10em}.pure-form input.pure-input-rounded,.pure-form .pure-input-rounded{border-radius:2em;padding:.5em 1em}.pure-form .pure-group fieldset{margin-bottom:10px}.pure-form .pure-group input{display:block;padding:10px;margin:0;border-radius:0;position:relative;top:-1px}.pure-form .pure-group input:focus{z-index:2}.pure-form .pure-group input:first-child{top:1px;border-radius:4px 4px 0 0}.pure-form .pure-group input:last-child{top:-2px;border-radius:0 0 4px 4px}.pure-form .pure-group button{margin:.35em 0}.pure-form .pure-input-1{width:100%}.pure-form .pure-input-2-3{width:66%}.pure-form .pure-input-1-2{width:50%}.pure-form .pure-input-1-3{width:33%}.pure-form .pure-input-1-4{width:25%}.pure-form .pure-help-inline,.pure-form-message-inline{display:inline-block;padding-left:.3em;color:#666;vertical-align:middle;font-size:.875em}.pure-form-message{display:block;color:#666;font-size:.875em}@media only screen and (max-width :480px){.pure-form button[type=submit]{margin:.7em 0 0}.pure-form input:not([type]),.pure-form input[type=text],.pure-form input[type=password],.pure-form input[type=email],.pure-form input[type=url],.pure-form input[type=date],.pure-form input[type=month],.pure-form input[type=time],.pure-form input[type=datetime],.pure-form input[type=datetime-local],.pure-form input[type=week],.pure-form input[type=number],.pure-form input[type=search],.pure-form input[type=tel],.pure-form input[type=color],.pure-form label{margin-bottom:.3em;display:block}.pure-group input:not([type]),.pure-group input[type=text],.pure-group input[type=password],.pure-group input[type=email],.pure-group input[type=url],.pure-group input[type=date],.pure-group input[type=month],.pure-group input[type=time],.pure-group input[type=datetime],.pure-group input[type=datetime-local],.pure-group input[type=week],.pure-group input[type=number],.pure-group input[type=search],.pure-group input[type=tel],.pure-group input[type=color]{margin-bottom:0}.pure-form-aligned .pure-control-group label{margin-bottom:.3em;text-align:left;display:block;width:100%}.pure-form-aligned .pure-controls{margin:1.5em 0 0}.pure-form .pure-help-inline,.pure-form-message-inline,.pure-form-message{display:block;font-size:.75em;padding:.2em 0 .8em}}.pure-menu ul{position:absolute;visibility:hidden}.pure-menu.pure-menu-open{visibility:visible;z-index:2;width:100%}.pure-menu ul{left:-10000px;list-style:none;margin:0;padding:0;top:-10000px;z-index:1}.pure-menu>ul{position:relative}.pure-menu-open>ul{left:0;top:0;visibility:visible}.pure-menu-open>ul:focus{outline:0}.pure-menu li{position:relative}.pure-menu a,.pure-menu .pure-menu-heading{display:block;color:inherit;line-height:1.5em;padding:5px 20px;text-decoration:none;white-space:nowrap}.pure-menu.pure-menu-horizontal>.pure-menu-heading{display:inline-block;*display:inline;zoom:1;margin:0;vertical-align:middle}.pure-menu.pure-menu-horizontal>ul{display:inline-block;*display:inline;zoom:1;vertical-align:middle}.pure-menu li a{padding:5px 20px}.pure-menu-can-have-children>.pure-menu-label:after{content:'\25B8';float:right;font-family:'Lucida Grande','Lucida Sans Unicode','DejaVu Sans',sans-serif;margin-right:-20px;margin-top:-1px}.pure-menu-can-have-children>.pure-menu-label{padding-right:30px}.pure-menu-separator{background-color:#dfdfdf;display:block;height:1px;font-size:0;margin:7px 2px;overflow:hidden}.pure-menu-hidden{display:none}.pure-menu-fixed{position:fixed;top:0;left:0;width:100%}.pure-menu-horizontal li{display:inline-block;*display:inline;zoom:1;vertical-align:middle}.pure-menu-horizontal li li{display:block}.pure-menu-horizontal>.pure-menu-children>.pure-menu-can-have-children>.pure-menu-label:after{content:"\25BE"}.pure-menu-horizontal>.pure-menu-children>.pure-menu-can-have-children>.pure-menu-label{padding-right:30px}.pure-menu-horizontal li.pure-menu-separator{height:50%;width:1px;margin:0 7px}.pure-menu-horizontal li li.pure-menu-separator{height:1px;width:auto;margin:7px 2px}.pure-menu.pure-menu-open,.pure-menu.pure-menu-horizontal li .pure-menu-children{background:#fff;border:1px solid #b7b7b7}.pure-menu.pure-menu-horizontal,.pure-menu.pure-menu-horizontal .pure-menu-heading{border:0}.pure-menu a{border:1px solid transparent;border-left:0;border-right:0}.pure-menu a,.pure-menu .pure-menu-can-have-children>li:after{color:#777}.pure-menu .pure-menu-can-have-children>li:hover:after{color:#fff}.pure-menu .pure-menu-open{background:#dedede}.pure-menu li a:hover,.pure-menu li a:focus{background:#eee}.pure-menu li.pure-menu-disabled a:hover,.pure-menu li.pure-menu-disabled a:focus{background:#fff;color:#bfbfbf}.pure-menu .pure-menu-disabled>a{background-image:none;border-color:transparent;cursor:default}.pure-menu .pure-menu-disabled>a,.pure-menu .pure-menu-can-have-children.pure-menu-disabled>a:after{color:#bfbfbf}.pure-menu .pure-menu-heading{color:#565d64;text-transform:uppercase;font-size:90%;margin-top:.5em;border-bottom-width:1px;border-bottom-style:solid;border-bottom-color:#dfdfdf}.pure-menu .pure-menu-selected a{color:#000}.pure-menu.pure-menu-open.pure-menu-fixed{border:0;border-bottom:1px solid #b7b7b7}.pure-paginator{letter-spacing:-.31em;*letter-spacing:normal;*word-spacing:-.43em;text-rendering:optimizespeed;list-style:none;margin:0;padding:0}.opera-only :-o-prefocus,.pure-paginator{word-spacing:-.43em}.pure-paginator li{display:inline-block;*display:inline;zoom:1;letter-spacing:normal;word-spacing:normal;vertical-align:top;text-rendering:auto}.pure-paginator .pure-button{border-radius:0;padding:.8em 1.4em;vertical-align:top;height:1.1em}.pure-paginator .pure-button:focus,.pure-paginator .pure-button:active{outline-style:none}.pure-paginator .prev,.pure-paginator .next{color:#C0C1C3;text-shadow:0 -1px 0 rgba(0,0,0,.45)}.pure-paginator .prev{border-radius:2px 0 0 2px}.pure-paginator .next{border-radius:0 2px 2px 0}@media (max-width:480px){.pure-menu-horizontal{width:100%}.pure-menu-children li{display:block;border-bottom:1px solid #000}}.pure-table{border-collapse:collapse;border-spacing:0;empty-cells:show;border:1px solid #cbcbcb}.pure-table caption{color:#000;font:italic 85%/1 arial,sans-serif;padding:1em 0;text-align:center}.pure-table td,.pure-table th{border-left:1px solid #cbcbcb;border-width:0 0 0 1px;font-size:inherit;margin:0;overflow:visible;padding:.5em 1em}.pure-table td:first-child,.pure-table th:first-child{border-left-width:0}.pure-table thead{background:#e0e0e0;color:#000;text-align:left;vertical-align:bottom}.pure-table td{background-color:transparent}.pure-table-odd td{background-color:#f2f2f2}.pure-table-striped tr:nth-child(2n-1) td{background-color:#f2f2f2}.pure-table-bordered td{border-bottom:1px solid #cbcbcb}.pure-table-bordered tbody>tr:last-child td,.pure-table-horizontal tbody>tr:last-child td{border-bottom-width:0}.pure-table-horizontal td,.pure-table-horizontal th{border-width:0 0 1px;border-bottom:1px solid #cbcbcb}.pure-table-horizontal tbody>tr:last-child td{border-bottom-width:0}
"""

##############################################################################
# Script setup and execution
##############################################################################


application = webapp.WSGIApplication([('%s'      % _WEB_TEST_DIR, MainTestPageHandler),
                                      ('%s/run'  % _WEB_TEST_DIR, JsonTestRunHandler),
                                      ('%s/list' % _WEB_TEST_DIR, JsonTestListHandler),
                                      ('%s/css' % _WEB_TEST_DIR, CSSHandler)],
                                      debug=True)

def main():
    run_wsgi_app(application)                                    

if __name__ == '__main__':
    main()

