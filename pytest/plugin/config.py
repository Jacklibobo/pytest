""" command line configuration, ini-file and conftest.py processing. """

import py
import sys, os
from pytest.main import PluginManager
import pytest

def pytest_cmdline_parse(pluginmanager, args):
    config = Config(pluginmanager)
    config.parse(args)
    if config.option.debug:
        config.trace.root.setwriter(sys.stderr.write)
    return config

class Parser:
    """ Parser for command line arguments. """

    def __init__(self, usage=None, processopt=None):
        self._anonymous = OptionGroup("custom options", parser=self)
        self._groups = []
        self._processopt = processopt
        self._usage = usage
        self._inidict = {}
        self.hints = []

    def processoption(self, option):
        if self._processopt:
            if option.dest:
                self._processopt(option)

    def addnote(self, note):
        self._notes.append(note)

    def getgroup(self, name, description="", after=None):
        """ get (or create) a named option Group.
       
        :name: unique name of the option group.
        :description: long description for --help output.
        :after: name of other group, used for ordering --help output.
        """
        for group in self._groups:
            if group.name == name:
                return group
        group = OptionGroup(name, description, parser=self)
        i = 0
        for i, grp in enumerate(self._groups):
            if grp.name == after:
                break
        self._groups.insert(i+1, group)
        return group

    def addoption(self, *opts, **attrs):
        """ add an optparse-style option. """
        self._anonymous.addoption(*opts, **attrs)

    def parse(self, args):
        self.optparser = optparser = MyOptionParser(self)
        groups = list(reversed(self._groups)) + [self._anonymous]
        for group in groups:
            if group.options:
                desc = group.description or group.name
                optgroup = py.std.optparse.OptionGroup(optparser, desc)
                optgroup.add_options(reversed(group.options))
                optparser.add_option_group(optgroup)
        return self.optparser.parse_args([str(x) for x in args])

    def parse_setoption(self, args, option):
        parsedoption, args = self.parse(args)
        for name, value in parsedoption.__dict__.items():
            setattr(option, name, value)
        return args

    def addini(self, name, help, type=None, default=None):
        """ add an ini-file option with the given name and description. """
        assert type in (None, "pathlist", "args", "linelist")
        self._inidict[name] = (help, type, default)

class OptionGroup:
    def __init__(self, name, description="", parser=None):
        self.name = name
        self.description = description
        self.options = []
        self.parser = parser

    def addoption(self, *optnames, **attrs):
        """ add an option to this group. """
        option = py.std.optparse.Option(*optnames, **attrs)
        self._addoption_instance(option, shortupper=False)

    def _addoption(self, *optnames, **attrs):
        option = py.std.optparse.Option(*optnames, **attrs)
        self._addoption_instance(option, shortupper=True)

    def _addoption_instance(self, option, shortupper=False):
        if not shortupper:
            for opt in option._short_opts:
                if opt[0] == '-' and opt[1].islower():
                    raise ValueError("lowercase shortoptions reserved")
        if self.parser:
            self.parser.processoption(option)
        self.options.append(option)


class MyOptionParser(py.std.optparse.OptionParser):
    def __init__(self, parser):
        self._parser = parser
        py.std.optparse.OptionParser.__init__(self, usage=parser._usage,
            add_help_option=False)
    def format_epilog(self, formatter):
        hints = self._parser.hints
        if hints:
            s = "\n".join(["hint: " + x for x in hints]) + "\n"
            s = "\n" + s + "\n"
            return s
        return ""

class Conftest(object):
    """ the single place for accessing values and interacting
        towards conftest modules from py.test objects.
    """
    def __init__(self, onimport=None, confcutdir=None):
        self._path2confmods = {}
        self._onimport = onimport
        self._conftestpath2mod = {}
        self._confcutdir = confcutdir
        self._md5cache = {}

    def setinitial(self, args):
        """ try to find a first anchor path for looking up global values
            from conftests. This function is usually called _before_
            argument parsing.  conftest files may add command line options
            and we thus have no completely safe way of determining
            which parts of the arguments are actually related to options
            and which are file system paths.  We just try here to get
            bootstrapped ...
        """
        current = py.path.local()
        opt = '--confcutdir'
        for i in range(len(args)):
            opt1 = str(args[i])
            if opt1.startswith(opt):
                if opt1 == opt:
                    if len(args) > i:
                        p = current.join(args[i+1], abs=True)
                elif opt1.startswith(opt + "="):
                    p = current.join(opt1[len(opt)+1:], abs=1)
                self._confcutdir = p
                break
        for arg in args + [current]:
            if hasattr(arg, 'startswith') and arg.startswith("--"):
                continue
            anchor = current.join(arg, abs=1)
            if anchor.check(): # we found some file object
                self._path2confmods[None] = self.getconftestmodules(anchor)
                # let's also consider test* dirs
                if anchor.check(dir=1):
                    for x in anchor.listdir("test*"):
                        if x.check(dir=1):
                            self.getconftestmodules(x)
                break
        else:
            assert 0, "no root of filesystem?"

    def getconftestmodules(self, path):
        """ return a list of imported conftest modules for the given path.  """
        try:
            clist = self._path2confmods[path]
        except KeyError:
            if path is None:
                raise ValueError("missing default confest.")
            dp = path.dirpath()
            clist = []
            if dp != path:
                cutdir = self._confcutdir
                if cutdir and path != cutdir and not path.relto(cutdir):
                    pass
                else:
                    conftestpath = path.join("conftest.py")
                    if conftestpath.check(file=1):
                        key = conftestpath.computehash()
                        # XXX logging about conftest loading
                        if key not in self._md5cache:
                            clist.append(self.importconftest(conftestpath))
                            self._md5cache[key] = conftestpath
                        else:
                            # use some kind of logging
                            print ("WARN: not loading %s" % conftestpath)
                clist[:0] = self.getconftestmodules(dp)
            self._path2confmods[path] = clist
        # be defensive: avoid changes from caller side to
        # affect us by always returning a copy of the actual list
        return clist[:]

    def rget(self, name, path=None):
        mod, value = self.rget_with_confmod(name, path)
        return value

    def rget_with_confmod(self, name, path=None):
        modules = self.getconftestmodules(path)
        modules.reverse()
        for mod in modules:
            try:
                return mod, getattr(mod, name)
            except AttributeError:
                continue
        raise KeyError(name)

    def importconftest(self, conftestpath):
        assert conftestpath.check(), conftestpath
        try:
            return self._conftestpath2mod[conftestpath]
        except KeyError:
            pkgpath = conftestpath.pypkgpath()
            if pkgpath is None:
                _ensure_removed_sysmodule(conftestpath.purebasename)
            self._conftestpath2mod[conftestpath] = mod = conftestpath.pyimport()
            dirpath = conftestpath.dirpath()
            if dirpath in self._path2confmods:
                for path, mods in self._path2confmods.items():
                    if path and path.relto(dirpath) or path == dirpath:
                        assert mod not in mods
                        mods.append(mod)
            self._postimport(mod)
            return mod

    def _postimport(self, mod):
        if self._onimport:
            self._onimport(mod)
        return mod

def _ensure_removed_sysmodule(modname):
    try:
        del sys.modules[modname]
    except KeyError:
        pass

class CmdOptions(object):
    """ holds cmdline options as attributes."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
    def __repr__(self):
        return "<CmdOptions %r>" %(self.__dict__,)

class Config(object):
    """ access to configuration values, pluginmanager and plugin hooks.  """
    basetemp = None

    def __init__(self, pluginmanager=None):
        #: command line option values, usually added via parser.addoption(...)
        #: or parser.getgroup(...).addoption(...) calls
        self.option = CmdOptions()
        self._parser = Parser(
            usage="usage: %prog [options] [file_or_dir] [file_or_dir] [...]",
            processopt=self._processopt,
        )
        #: a pluginmanager instance
        self.pluginmanager = pluginmanager or PluginManager(load=True)
        self.trace = self.pluginmanager.trace.root.get("config")
        self._conftest = Conftest(onimport=self._onimportconftest)
        self.hook = self.pluginmanager.hook

    def _onimportconftest(self, conftestmodule):
        self.trace("loaded conftestmodule %r" %(conftestmodule,))
        self.pluginmanager.consider_conftest(conftestmodule)

    def _processopt(self, opt):
        if hasattr(opt, 'default') and opt.dest:
            if not hasattr(self.option, opt.dest):
                setattr(self.option, opt.dest, opt.default)

    def _getmatchingplugins(self, fspath):
        allconftests = self._conftest._conftestpath2mod.values()
        plugins = [x for x in self.pluginmanager.getplugins()
                        if x not in allconftests]
        plugins += self._conftest.getconftestmodules(fspath)
        return plugins

    def _setinitialconftest(self, args):
        # capture output during conftest init (#issue93)
        name = hasattr(os, 'dup') and 'StdCaptureFD' or 'StdCapture'
        cap = getattr(py.io, name)()
        try:
            try:
                self._conftest.setinitial(args)
            finally:
                out, err = cap.reset()
        except:
            sys.stdout.write(out)
            sys.stderr.write(err)
            raise

    def _initini(self, args):
        self.inicfg = getcfg(args, ["setup.cfg", "tox.ini",])
        self._parser.addini('addopts', 'extra command line options', 'args')
        self._parser.addini('minversion', 'minimally required pytest version')

    def _preparse(self, args, addopts=True):
        self._initini(args)
        if addopts:
            args[:] = self.getini("addopts") + args
        self._checkversion()
        self.pluginmanager.consider_setuptools_entrypoints()
        self.pluginmanager.consider_env()
        self.pluginmanager.consider_preparse(args)
        self._setinitialconftest(args)
        self.hook.pytest_addoption(parser=self._parser)

    def _checkversion(self):
        minver = self.inicfg.get('minversion', None)
        if minver:
            ver = minver.split(".")
            myver = pytest.__version__.split(".")
            if myver < ver:
                raise pytest.UsageError(
                    "%s:%d: requires pytest-%s, actual pytest-%s'" %(
                    self.inicfg.config.path, self.inicfg.lineof('minversion'),
                    minver, pytest.__version__))

    def parse(self, args):
        # parse given cmdline arguments into this config object.
        # Note that this can only be called once per testing process.
        assert not hasattr(self, 'args'), (
                "can only parse cmdline args at most once per Config object")
        self._preparse(args)
        self._parser.hints.extend(self.pluginmanager._hints)
        args = self._parser.parse_setoption(args, self.option)
        if not args:
            args.append(py.std.os.getcwd())
        self.args = args

    def ensuretemp(self, string, dir=True):
        return self.getbasetemp().ensure(string, dir=dir)

    def getbasetemp(self):
        if self.basetemp is None:
            basetemp = self.option.basetemp
            if basetemp:
                basetemp = py.path.local(basetemp)
                if not basetemp.check(dir=1):
                    basetemp.mkdir()
            else:
                basetemp = py.path.local.make_numbered_dir(prefix='pytest-')
            self.basetemp = basetemp
        return self.basetemp

    def mktemp(self, basename, numbered=False):
        basetemp = self.getbasetemp()
        if not numbered:
            return basetemp.mkdir(basename)
        else:
            return py.path.local.make_numbered_dir(prefix=basename,
                keep=0, rootdir=basetemp, lock_timeout=None)

    def getini(self, name):
        """ return configuration value from an ini file. If the
        specified name hasn't been registered through a prior ``parse.addini``
        call (usually from a plugin), a ValueError is raised. """
        try:
            description, type, default = self._parser._inidict[name]
        except KeyError:
            raise ValueError("unknown configuration value: %r" %(name,))
        try:
            value = self.inicfg[name]
        except KeyError:
            if default is not None:
                return default
            if type is None:
                return ''
            return []
        if type == "pathlist":
            dp = py.path.local(self.inicfg.config.path).dirpath()
            l = []
            for relpath in py.std.shlex.split(value):
                l.append(dp.join(relpath, abs=True))
            return l
        elif type == "args":
            return py.std.shlex.split(value)
        elif type == "linelist":
            return filter(None, map(lambda x: x.strip(), value.split("\n")))
        else:
            assert type is None
            return value

    def _getconftest_pathlist(self, name, path=None):
        try:
            mod, relroots = self._conftest.rget_with_confmod(name, path)
        except KeyError:
            return None
        modpath = py.path.local(mod.__file__).dirpath()
        l = []
        for relroot in relroots:
            if not isinstance(relroot, py.path.local):
                relroot = relroot.replace("/", py.path.local.sep)
                relroot = modpath.join(relroot, abs=True)
            l.append(relroot)
        return l

    def _getconftest(self, name, path=None, check=False):
        if check:
            self._checkconftest(name)
        return self._conftest.rget(name, path)

    def getvalue(self, name, path=None):
        """ return ``name`` value looked set from command line options.

        (deprecated) if we can't find the option also lookup
        the name in a matching conftest file.
        """
        try:
            return getattr(self.option, name)
        except AttributeError:
            return self._getconftest(name, path, check=False)

    def getvalueorskip(self, name, path=None):
        """ (deprecated) return getvalue(name) or call py.test.skip if no value exists. """
        try:
            val = self.getvalue(name, path)
            if val is None:
                raise KeyError(name)
            return val
        except KeyError:
            py.test.skip("no %r value found" %(name,))


def getcfg(args, inibasenames):
    args = [x for x in args if str(x)[0] != "-"]
    if not args:
        args = [py.path.local()]
    for inibasename in inibasenames:
        for p in args:
            x = findupwards(p, inibasename)
            if x is not None:
                iniconfig = py.iniconfig.IniConfig(x)
                if 'pytest' in iniconfig.sections:
                    return iniconfig['pytest']
    return {}
   
def findupwards(current, basename):
    current = py.path.local(current)
    while 1:
        p = current.join(basename)
        if p.check():
            return p
        p = current.dirpath()
        if p == current:
            return
        current = p
