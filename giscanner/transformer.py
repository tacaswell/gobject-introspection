# -*- Mode: Python -*-
# GObject-Introspection - a framework for introspecting GObject libraries
# Copyright (C) 2008  Johan Dahlin
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.
#

import os
import sys
import re

from . import ast
from . import glibast
from .cachestore import CacheStore
from .config import DATADIR, GIR_DIR, GIR_SUFFIX
from .girparser import GIRParser
from .sourcescanner import (
    SourceSymbol, ctype_name, CTYPE_POINTER,
    CTYPE_BASIC_TYPE, CTYPE_UNION, CTYPE_ARRAY, CTYPE_TYPEDEF,
    CTYPE_VOID, CTYPE_ENUM, CTYPE_FUNCTION, CTYPE_STRUCT,
    CSYMBOL_TYPE_FUNCTION, CSYMBOL_TYPE_TYPEDEF, CSYMBOL_TYPE_STRUCT,
    CSYMBOL_TYPE_ENUM, CSYMBOL_TYPE_UNION, CSYMBOL_TYPE_OBJECT,
    CSYMBOL_TYPE_MEMBER, CSYMBOL_TYPE_ELLIPSIS, CSYMBOL_TYPE_CONST,
    TYPE_QUALIFIER_CONST)

class TypeResolutionException(Exception):
    pass

_xdg_data_dirs = [x for x in os.environ.get('XDG_DATA_DIRS', '').split(':') \
                      + [DATADIR, '/usr/share'] if x]

class Transformer(object):
    namespace = property(lambda self: self._namespace)

    UCASE_CONSTANT_RE = re.compile(r'[_A-Z0-9]+')

    def __init__(self, namespace, accept_unprefixed=False):
        self._cwd = os.getcwd() + os.sep
        self._cachestore = CacheStore()
        self._accept_unprefixed = accept_unprefixed
        self._namespace = namespace
        self._pkg_config_packages = set()
        self._typedefs_ns = {}
        self._enable_warnings = False
        self._warned = False
        self._includes = {}
        self._include_names = set()
        self._includepaths = []

    def get_includes(self):
        return self._include_names

    def enable_warnings(self, enable):
        self._enable_warnings = enable

    def did_warn(self):
        return self._warned

    def get_pkgconfig_packages(self):
        return self._pkg_config_packages

    def _append_new_node(self, node):
        original = self._namespace.get(node.name)
        # Special case constants here; we allow duplication to sort-of
        # handle #ifdef.  But this introduces an arch-dependency in the .gir
        # file.  So far this has only come up scanning glib - in theory, other
        # modules will just depend on that.
        if isinstance(original, ast.Constant) and isinstance(node, ast.Constant):
            pass
        elif original:
            positions = set()
            positions.update(original.file_positions)
            positions.update(node.file_positions)
            self.log_warning("Namespace conflict for '%s'" % (node.name, ),
                             positions, fatal=True)
        else:
            self._namespace.append(node)

    def parse(self, symbols):
        for symbol in symbols:
            node = self._traverse_one(symbol)
            if node:
                self._append_new_node(node)

        # Now look through the namespace for things like
        # typedef struct _Foo Foo;
        # where we've never seen the struct _Foo.  Just create
        # an empty structure for these as "disguised"
        # If we do have a class/interface, merge fields
        for typedef, compound in self._typedefs_ns.iteritems():
            ns_compound = self._namespace.get(compound.name)
            if not ns_compound:
                ns_compound = self._namespace.get('_' + compound.name)
            if (not ns_compound and isinstance(compound, (ast.Record, ast.Union))
                and len(compound.fields) == 0):
                disguised = ast.Record(compound.name, typedef, disguised=True)
                self._namespace.append(disguised)
            elif not ns_compound:
                self._namespace.append(compound)
            elif isinstance(ns_compound, (ast.Record, ast.Union)) and len(ns_compound.fields) == 0:
                ns_compound.fields = compound.fields
        self._typedefs_ns = None

    def set_include_paths(self, paths):
        self._includepaths = list(paths)

    def register_include(self, include):
        if include in self._include_names:
            return
        filename = self._find_include(include)
        self._parse_include(filename)
        self._include_names.add(include)

    def lookup_giname(self, name):
        """Given a name of the form Foo or Bar.Foo,
return the corresponding ast.Node, or None if none
available.  Will throw KeyError however for unknown
namespaces."""
        if '.' not in name:
            return self._namespace.get(name)
        else:
            (ns, name) = name.split('.', 1)
            if ns == self._namespace.name:
                return self._namespace.get(name)
            include = self._includes[ns]
            return include.get(name)

    def lookup_typenode(self, typeobj):
        """Given a Type object, if it points to a giname,
calls lookup_giname() on the name.  Otherwise return
None."""
        if typeobj.target_giname:
            return self.lookup_giname(typeobj.target_giname)
        return None

    # Private

    def log_warning(self, text, file_positions=None, prefix=None,
                    fatal=False):
        """Log a warning, using optional file positioning information.
If the warning is related to a ast.Node type, see log_node_warning()."""
        if not fatal and not self._enable_warnings:
            return

        self._warned = True

        if file_positions is None or len(file_positions) == 0:
            target_file_positions = [('<unknown>', -1, -1)]
        else:
            target_file_positions = file_positions

        position_strings = []
        for (filename, line, column) in target_file_positions:
            if filename.startswith(self._cwd):
                filename = filename[len(self._cwd):]
            if column != -1:
                position = '%s:%d:%d' % (filename, line, column)
            elif line != -1:
                position = '%s:%d' % (filename, line, )
            else:
                position = '%s:' % (filename, )
            position_strings.append(position)

        for position in position_strings[:-1]:
            print >>sys.stderr, "%s:" % (position, )
        last_position = position_strings[-1]
        error_type = 'error' if fatal else 'warning'
        if prefix:
            print >>sys.stderr, \
'''%s: %s: %s: %s: %s''' % (last_position, error_type, self._namespace.name,
                                 prefix, text)
        else:
            print >>sys.stderr, \
'''%s: %s: %s: %s''' % (last_position, error_type, self._namespace.name, text)
        if fatal:
            sys.exit(1)

    def log_symbol_warning(self, symbol, text, **kwargs):
        """Log a warning in the context of the given symbol."""
        if symbol.source_filename:
            file_positions = [(symbol.source_filename, symbol.line, -1)]
        else:
            file_positions = None
        prefix = "symbol=%r" % (symbol.ident, )
        self.log_warning(text, file_positions, prefix=prefix, **kwargs)

    def log_node_warning(self, node, text, context=None, fatal=False):
        """Log a warning, using information about file positions from
the given node.  The optional context argument, if given, should be
another ast.Node type which will also be displayed.  If no file position
information is available from the node, the position data from the
context will be used."""
        if hasattr(node, 'file_positions'):
            if (len(node.file_positions) == 0 and
                (context is not None) and len(context.file_positions) > 0):
                file_positions = context.file_positions
            else:
                file_positions = node.file_positions
        else:
            file_positions = None
            if not context:
                text = "context=%r %s" % (node, text)

        if context:
            if isinstance(context, ast.Function):
                name = context.symbol
            else:
                name = context.name
            text = "%s: %s" % (name, text)
        elif len(file_positions) == 0 and hasattr(node, 'name'):
            text = "(%s)%s: %s" % (node.__class__.__name__, node.name, text)

        self.log_warning(text, file_positions, fatal=fatal)

    def _find_include(self, include):
        searchdirs = self._includepaths[:]
        for path in _xdg_data_dirs:
            searchdirs.append(os.path.join(path, GIR_SUFFIX))
        searchdirs.append(GIR_DIR)

        girname = '%s-%s.gir' % (include.name, include.version)
        for d in searchdirs:
            path = os.path.join(d, girname)
            if os.path.exists(path):
                return path
        sys.stderr.write("Couldn't find include %r (search path: %r)\n"\
                         % (girname, searchdirs))
        sys.exit(1)

    def _parse_include(self, filename):
        parser = self._cachestore.load(filename)
        if parser is None:
            parser = GIRParser()
            parser.parse(filename)
            self._cachestore.store(filename, parser)

        for include in parser.get_includes():
            self.register_include(include)

        for pkg in parser.get_pkgconfig_packages():
            self._pkg_config_packages.add(pkg)
        namespace = parser.get_namespace()
        self._includes[namespace.name] = namespace

    def _iter_namespaces(self):
        """Return an iterator over all included namespaces; the
currently-scanned namespace is first."""
        yield self._namespace
        for ns in self._includes.itervalues():
            yield ns

    def _sort_matches(self, x, y):
        if x[0] is self._namespace:
            return 1
        elif y[0] is self._namespace:
            return -1
        return cmp(x[2], y[2])

    def _split_c_string_for_namespace_matches(self, name, is_identifier=False):
        matches = []  # Namespaces which might contain this name
        unprefixed_namespaces = [] # Namespaces with no prefix, last resort
        for ns in self._iter_namespaces():
            if is_identifier:
                prefixes = ns.identifier_prefixes
            else:
                prefixes = ns.symbol_prefixes
            if prefixes:
                for prefix in prefixes:
                    if (not is_identifier) and (not prefix.endswith('_')):
                        prefix = prefix + '_'
                    if name.startswith(prefix):
                        matches.append((ns, name[len(prefix):], len(prefix)))
                        break
            else:
                unprefixed_namespaces.append(ns)
        if matches:
            matches.sort(self._sort_matches)
            return map(lambda x: (x[0], x[1]), matches)
        elif self._accept_unprefixed:
            return [(self._namespace, name)]
        elif unprefixed_namespaces:
            # A bit of a hack; this function ideally shouldn't look through the
            # contents of namespaces; but since we aren't scanning anything
            # without a prefix, it's not too bad.
            for ns in unprefixed_namespaces:
                if name in ns:
                    return [(ns, name)]
        raise ValueError("Unknown namespace for %s %r"
                         % ('identifier' if is_identifier else 'symbol', name, ))

    def split_ctype_namespaces(self, ident):
        """Given a StudlyCaps string identifier like FooBar, return a
list of (namespace, stripped_identifier) sorted by namespace length,
or raise ValueError.  As a special case, if the current namespace matches,
it is always biggest (i.e. last)."""
        return self._split_c_string_for_namespace_matches(ident, is_identifier=True)

    def split_csymbol_namespaces(self, symbol):
        """Given a C symbol like foo_bar_do_baz, return a list of
(namespace, stripped_symbol) sorted by namespace match probablity, or
raise ValueError."""
        return self._split_c_string_for_namespace_matches(symbol, is_identifier=False)

    def split_csymbol(self, symbol):
        """Given a C symbol like foo_bar_do_baz, return the most probable
(namespace, stripped_symbol) match, or raise ValueError."""
        matches = self._split_c_string_for_namespace_matches(symbol, is_identifier=False)
        return matches[-1]

    def strip_identifier_or_warn(self, ident, fatal=False):
        hidden = ident.startswith('_')
        if hidden:
            ident = ident[1:]
        try:
            matches = self.split_ctype_namespaces(ident)
        except ValueError, e:
            self.log_warning(str(e), fatal=fatal)
            return None
        for ns, name in matches:
            if ns is self._namespace:
                if hidden:
                    return '_' + name
                return name
        (ns, name) = matches[-1]
        self.log_warning("Skipping foreign identifier %r from namespace %s" % (ident, ns.name, ),
                         fatal=fatal)
        return None

    def _strip_symbol_or_warn(self, symbol, is_constant=False, fatal=False):
        ident = symbol.ident
        if is_constant:
            # Temporarily lowercase
            ident = ident.lower()
        hidden = ident.startswith('_')
        if hidden:
            ident = ident[1:]
        try:
            (ns, name) = self.split_csymbol(ident)
        except ValueError, e:
            self.log_symbol_warning(symbol, "Unknown namespace", fatal=fatal)
            return None
        if ns != self._namespace:
            self.log_symbol_warning(symbol,
"Skipping foreign symbol from namespace %s" % (ns.name, ),
                                    fatal=fatal)
            return None
        if is_constant:
            name = name.upper()
        if hidden:
            return '_' + name
        return name

    def _traverse_one(self, symbol, stype=None):
        assert isinstance(symbol, SourceSymbol), symbol

        if stype is None:
            stype = symbol.type
        if stype == CSYMBOL_TYPE_FUNCTION:
            return self._create_function(symbol)
        elif stype == CSYMBOL_TYPE_TYPEDEF:
            return self._create_typedef(symbol)
        elif stype == CSYMBOL_TYPE_STRUCT:
            return self._create_struct(symbol)
        elif stype == CSYMBOL_TYPE_ENUM:
            return self._create_enum(symbol)
        elif stype == CSYMBOL_TYPE_MEMBER:
            return self._create_member(symbol)
        elif stype == CSYMBOL_TYPE_UNION:
            return self._create_union(symbol)
        elif stype == CSYMBOL_TYPE_CONST:
            return self._create_const(symbol)
        # Ignore variable declarations in the header
        elif stype == CSYMBOL_TYPE_OBJECT:
            pass
        else:
            print 'transformer: unhandled symbol: %r' % (symbol, )

    def _enum_common_prefix(self, symbol):
        def common_prefix(a, b):
            commonparts = []
            for aword, bword in zip(a.split('_'), b.split('_')):
                if aword != bword:
                    return '_'.join(commonparts) + '_'
                commonparts.append(aword)
            return min(a, b)

        # Nothing less than 2 has a common prefix
        if len(list(symbol.base_type.child_list)) < 2:
            return None
        prefix = None
        for child in symbol.base_type.child_list:
            if prefix is None:
                prefix = child.ident
            else:
                prefix = common_prefix(prefix, child.ident)
                if prefix == '':
                    return None
        return prefix

    def _create_enum(self, symbol):
        prefix = self._enum_common_prefix(symbol)
        if prefix:
            prefixlen = len(prefix)
        else:
            prefixlen = 0
        members = []
        for child in symbol.base_type.child_list:
            if prefixlen > 0:
                name = child.ident[prefixlen:]
            else:
                # Ok, the enum members don't have a consistent prefix
                # among them, so let's just remove the global namespace
                # prefix.
                name = self._strip_symbol_or_warn(child, is_constant=True)
                if name is None:
                    return None
            members.append(ast.Member(name.lower(),
                                  child.const_int,
                                  child.ident))

        enum_name = self.strip_identifier_or_warn(symbol.ident)
        if not enum_name:
            return None
        if symbol.base_type.is_bitfield:
            klass = ast.Bitfield
        else:
            klass = ast.Enum
        node = klass(enum_name, symbol.ident, members)
        node.add_symbol_reference(symbol)
        return node

    def _create_function(self, symbol):
        parameters = list(self._create_parameters(symbol.base_type))
        return_ = self._create_return(symbol.base_type.base_type)
        name = self._strip_symbol_or_warn(symbol)
        if not name:
            return None
        func = ast.Function(name, return_, parameters, False, symbol.ident)
        func.add_symbol_reference(symbol)
        return func

    def _create_source_type(self, source_type):
        if source_type is None:
            return 'None'
        if source_type.type == CTYPE_VOID:
            value = 'void'
        elif source_type.type == CTYPE_BASIC_TYPE:
            value = source_type.name
        elif source_type.type == CTYPE_TYPEDEF:
            value = source_type.name
        elif source_type.type == CTYPE_ARRAY:
            return self._create_source_type(source_type.base_type)
        elif source_type.type == CTYPE_POINTER:
            value = self._create_source_type(source_type.base_type) + '*'
        else:
            value = 'gpointer'
        return value

    def _create_parameters(self, base_type):
        # warn if we see annotations for unknown parameters
        param_names = set(child.ident for child in base_type.child_list)
        for child in base_type.child_list:
            yield self._create_parameter(child)

    def _create_member(self, symbol):
        source_type = symbol.base_type
        if (source_type.type == CTYPE_POINTER and
            symbol.base_type.base_type.type == CTYPE_FUNCTION):
            node = self._create_callback(symbol, member=True)
        elif source_type.type == CTYPE_STRUCT and source_type.name is None:
            node = self._create_struct(symbol, anonymous=True)
        elif source_type.type == CTYPE_UNION and source_type.name is None:
            node = self._create_union(symbol, anonymous=True)
        else:
            # Special handling for fields; we don't have annotations on them
            # to apply later, yet.
            if source_type.type == CTYPE_ARRAY:
                ctype = self._create_source_type(source_type)
                canonical_ctype = self._canonicalize_ctype(ctype)
                if canonical_ctype[-1] == '*':
                    derefed_name = canonical_ctype[:-1]
                else:
                    derefed_name = canonical_ctype
                ftype = ast.Array(None, self.create_type_from_ctype_string(ctype),
                                  ctype=derefed_name)
                child_list = list(symbol.base_type.child_list)
                ftype.zeroterminated = False
                if child_list:
                    ftype.size = child_list[0].const_int
            else:
                ftype = self._create_type_from_base(symbol.base_type)
            # ast.Fields are assumed to be read-write
            # (except for Objects, see also glibtransformer.py)
            node = ast.Field(symbol.ident, ftype,
                         readable=True, writable=True, bits=symbol.const_int)
        return node

    def _create_typedef(self, symbol):
        ctype = symbol.base_type.type
        if (ctype == CTYPE_POINTER and
            symbol.base_type.base_type.type == CTYPE_FUNCTION):
            node = self._create_typedef_callback(symbol)
        elif (ctype == CTYPE_POINTER and
            symbol.base_type.base_type.type == CTYPE_STRUCT):
            node = self._create_typedef_struct(symbol, disguised=True)
        elif ctype == CTYPE_STRUCT:
            node = self._create_typedef_struct(symbol)
        elif ctype == CTYPE_UNION:
            node = self._create_typedef_union(symbol)
        elif ctype == CTYPE_ENUM:
            return self._create_enum(symbol)
        elif ctype in (CTYPE_TYPEDEF,
                       CTYPE_POINTER,
                       CTYPE_BASIC_TYPE,
                       CTYPE_VOID):
            name = self.strip_identifier_or_warn(symbol.ident)
            if not name:
                return None
            if symbol.base_type.name:
                target = self.create_type_from_ctype_string(symbol.base_type.name)
            else:
                target = ast.TYPE_ANY
            if name in ast.type_names:
                return None
            return ast.Alias(name, target, ctype=symbol.ident)
        else:
            raise NotImplementedError(
                "symbol %r of type %s" % (symbol.ident, ctype_name(ctype)))
        return node

    def _canonicalize_ctype(self, ctype):
        # First look up the ctype including any pointers;
        # a few type names like 'char*' have their own aliases
        # and we need pointer information for those.
        firstpass = ast.type_names.get(ctype)

        # If we have a particular alias for this, skip deep
        # canonicalization to prevent changing
        # e.g. char* -> int8*
        if firstpass:
            return firstpass.target_fundamental

        if not ctype.endswith('*'):
            return ctype

        # We have a pointer type.
        # Strip the end pointer, canonicalize our base type
        base = ctype[:-1]
        canonical_base = self._canonicalize_ctype(base)

        # Append the pointer again
        canonical = canonical_base + '*'

        return canonical

    def parse_ctype(self, ctype, is_member=False):
        canonical = self._canonicalize_ctype(ctype)

        # Remove all pointers - we require standard calling
        # conventions.  For example, an 'int' is always passed by
        # value (unless it's out or inout).
        derefed_typename = canonical.replace('*', '')

        # Preserve "pointerness" of struct/union members
        if (is_member and canonical.endswith('*') and
            derefed_typename in ast.basic_type_names):
            return 'gpointer'
        else:
            return derefed_typename

    def _create_type_from_base(self, source_type, is_parameter=False, is_return=False):
        ctype = self._create_source_type(source_type)
        const = ((source_type.type == CTYPE_POINTER) and
                 (source_type.base_type.type_qualifier & TYPE_QUALIFIER_CONST))
        return self.create_type_from_ctype_string(ctype, is_const=const,
                                                  is_parameter=is_parameter, is_return=is_return)

    def _create_bare_container_type(self, base, ctype=None,
                                    is_const=False):
        if base in ('GList', 'GSList', 'GLib.List', 'GLib.SList'):
            if base in ('GList', 'GSList'):
                name = 'GLib.' + base[1:]
            else:
                name = base
            return ast.List(name, ast.TYPE_ANY, ctype=ctype,
                        is_const=is_const)
        elif base in ('GArray', 'GPtrArray', 'GByteArray',
                      'GLib.Array', 'GLib.PtrArray', 'GLib.ByteArray'):
            if base in ('GArray', 'GPtrArray', 'GByteArray'):
                name = 'GLib.' + base[1:]
            else:
                name = base
            return ast.Array(name, ast.TYPE_ANY, ctype=ctype,
                         is_const=is_const)
        elif base in ('GHashTable', 'GLib.HashTable'):
            return ast.Map(ast.TYPE_ANY, ast.TYPE_ANY, ctype=ctype, is_const=is_const)
        return None

    def create_type_from_ctype_string(self, ctype, is_const=False,
                                      is_parameter=False, is_return=False):
        canonical = self._canonicalize_ctype(ctype)
        base = canonical.replace('*', '')

        # Special default: char ** -> ast.Array, same for GStrv
        if (is_return and canonical == 'utf8*') or base == 'GStrv':
            bare_utf8 = ast.TYPE_STRING.clone()
            bare_utf8.ctype = None
            return ast.Array(None, bare_utf8, ctype=ctype,
                             is_const=is_const)

        fundamental = ast.type_names.get(base)
        if fundamental is not None:
            return ast.Type(target_fundamental=fundamental.target_fundamental,
                        ctype=ctype,
                        is_const=is_const)
        container = self._create_bare_container_type(base, ctype=ctype, is_const=is_const)
        if container:
            return container
        return ast.Type(ctype=ctype, is_const=is_const)

    def _create_parameter(self, symbol):
        if symbol.type == CSYMBOL_TYPE_ELLIPSIS:
            ptype = ast.Varargs()
        else:
            ptype = self._create_type_from_base(symbol.base_type, is_parameter=True)
        return ast.Parameter(symbol.ident, ptype)

    def _create_return(self, source_type):
        typeval = self._create_type_from_base(source_type, is_return=True)
        return ast.Return(typeval)

    def _create_const(self, symbol):
        # Don't create constants for non-public things
        # http://bugzilla.gnome.org/show_bug.cgi?id=572790
        if (symbol.source_filename is None or
            not symbol.source_filename.endswith('.h')):
            return None
        # ignore non-uppercase defines
        if not self.UCASE_CONSTANT_RE.match(symbol.ident):
            return None
        name = self._strip_symbol_or_warn(symbol, is_constant=True)
        if not name:
            return None
        if symbol.const_string is not None:
            typeval = ast.TYPE_STRING
            value = symbol.const_string
        elif symbol.const_int is not None:
            typeval = ast.TYPE_INT
            value = '%d' % (symbol.const_int, )
        elif symbol.const_double is not None:
            typeval = ast.TYPE_DOUBLE
            value = '%f' % (symbol.const_double, )
        else:
            raise AssertionError()

        const = ast.Constant(name, typeval, value)
        const.add_symbol_reference(symbol)
        return const

    def _create_typedef_struct(self, symbol, disguised=False):
        name = self.strip_identifier_or_warn(symbol.ident)
        if not name:
            return None
        struct = ast.Record(name, symbol.ident, disguised)
        self._parse_fields(symbol, struct)
        struct.add_symbol_reference(symbol)
        self._typedefs_ns[symbol.ident] = struct
        return None

    def _create_typedef_union(self, symbol):
        name = self.strip_identifier_or_warn(symbol.ident)
        if not name:
            return None
        union = ast.Union(name, symbol.ident)
        self._parse_fields(symbol, union)
        union.add_symbol_reference(symbol)
        self._typedefs_ns[symbol.ident] = union
        return None

    def _create_typedef_callback(self, symbol):
        callback = self._create_callback(symbol)
        if not callback:
            return None
        self._typedefs_ns[callback.name] = callback
        return callback

    def _parse_fields(self, symbol, compound):
        for child in symbol.base_type.child_list:
            child_node = self._traverse_one(child)
            if not child_node:
                continue
            if isinstance(child_node, ast.Field):
                field = child_node
            else:
                field = ast.Field(child.ident, None, True, False,
                              anonymous_node=child_node)
            compound.fields.append(field)

    def _create_compound(self, klass, symbol, anonymous):
        if symbol.ident is None:
            # the compound is an anonymous member of another union or a struct
            assert anonymous
            compound = klass(None, None)
        else:
            compound = self._typedefs_ns.get(symbol.ident, None)

        if compound is None:
            # This is a bit of a hack; really we should try
            # to resolve through the typedefs to find the real
            # name
            if symbol.ident.startswith('_'):
                compound = self._typedefs_ns.get(symbol.ident[1:], None)
            if compound is None:
                if anonymous:
                    name = symbol.ident
                else:
                    name = self.strip_identifier_or_warn(symbol.ident)
                    if not name:
                        return None
                compound = klass(name, symbol.ident)

        self._parse_fields(symbol, compound)
        compound.add_symbol_reference(symbol)
        return compound

    def _create_struct(self, symbol, anonymous=False):
        return self._create_compound(ast.Record, symbol, anonymous)

    def _create_union(self, symbol, anonymous=False):
        return self._create_compound(ast.Union, symbol, anonymous)

    def _create_callback(self, symbol, member=False):
        parameters = list(self._create_parameters(symbol.base_type.base_type))
        retval = self._create_return(symbol.base_type.base_type.base_type)

        # Mark the 'user_data' arguments
        for i, param in enumerate(parameters):
            if (param.type.target_fundamental == 'gpointer' and
                param.argname == 'user_data'):
                param.closure_name = param.argname

        if member:
            name = symbol.ident
        elif symbol.ident.find('_') > 0:
            name = self._strip_symbol_or_warn(symbol)
            if not name:
                return None
        else:
            name = self.strip_identifier_or_warn(symbol.ident)
            if not name:
                return None
        callback = ast.Callback(name, retval, parameters, False)
        callback.add_symbol_reference(symbol)

        return callback

    def create_type_from_user_string(self, typestr):
        """Parse a C type string (as might be given from an
        annotation) and resolve it.  For compatibility, we can consume
both GI type string (utf8, Foo.Bar) style, as well as C (char *, FooBar) style.

Note that type resolution may not succeed."""
        if '.' in typestr:
            container = self._create_bare_container_type(typestr)
            if container:
                return container
            return self._namespace.type_from_name(typestr)
        typeval = self.create_type_from_ctype_string(typestr)
        self.resolve_type(typeval)
        # Explicitly clear out the c_type; there isn't one in this case.
        typeval.ctype = None
        return typeval

    def create_type_from_gtype_name(self, gtype_name):
        """Parse a GType name (as from g_type_name()), and return a
Type instance.  Note that this function performs namespace lookup,
in contrast to the other create_type() functions."""
        # First, is it a fundamental?
        fundamental = ast.type_names.get(gtype_name)
        if fundamental is not None:
            return ast.Type(target_fundamental=fundamental.target_fundamental)
        return ast.Type(gtype_name=gtype_name)

    def _resolve_type_from_ctype(self, typeval):
        assert typeval.ctype is not None
        pointer_stripped = typeval.ctype.replace('*', '')
        try:
            matches = self.split_ctype_namespaces(pointer_stripped)
        except ValueError, e:
            raise TypeResolutionException(e)
        target_giname = None
        for namespace, name in matches:
            target = namespace.get(name)
            if not target:
                target = namespace.get_by_ctype(pointer_stripped)
            if target:
                typeval.target_giname = '%s.%s' % (namespace.name, target.name)
                return True
        return False

    def _resolve_type_from_gtype_name(self, typeval):
        assert typeval.gtype_name is not None
        for ns in self._iter_namespaces():
            for node in ns.itervalues():
                if not isinstance(node, (ast.Class, ast.Interface,
                                         glibast.GLibBoxed,
                                         glibast.GLibEnum,
                                         glibast.GLibFlags)):
                    continue
                if node.type_name == typeval.gtype_name:
                    typeval.target_giname = '%s.%s' % (ns.name, node.name)
                    return True
        return False

    def resolve_type(self, typeval):
        if isinstance(typeval, (ast.Array, ast.List)):
            return self.resolve_type(typeval.element_type)
        elif isinstance(typeval, ast.Map):
            key_resolved = self.resolve_type(typeval.key_type)
            value_resolved = self.resolve_type(typeval.value_type)
            return key_resolved and value_resolved
        elif typeval.resolved:
            return True
        elif typeval.ctype:
            return self._resolve_type_from_ctype(typeval)
        elif typeval.gtype_name:
            return self._resolve_type_from_gtype_name(typeval)

    def _typepair_to_str(self, item):
        nsname, item = item
        if nsname is None:
            return item.name
        return '%s.%s' % (nsname, item.name)

    def gtypename_to_giname(self, gtname, names):
        resolved = names.type_names.get(gtname)
        if resolved:
            return self._typepair_to_str(resolved)
        resolved = self._names.type_names.get(gtname)
        if resolved:
            return self._typepair_to_str(resolved)
        raise KeyError("Failed to resolve GType name: %r" % (gtname, ))

    def ctype_of(self, obj):
        if hasattr(obj, 'ctype'):
            return obj.ctype
        elif hasattr(obj, 'symbol'):
            return obj.symbol
        else:
            return None

    def follow_aliases(self, type_name, names):
        while True:
            resolved = names.aliases.get(type_name)
            if resolved:
                (ns, alias) = resolved
                type_name = alias.target
            else:
                break
        return type_name
