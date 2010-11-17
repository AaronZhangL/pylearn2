#!/usr/bin/env python
__docformat__ = 'restructuredtext en'

import difflib
import operator
import os
import string
from StringIO import StringIO
from subprocess import Popen, PIPE
import sys
import tabnanny
import tokenize

import argparse
import reindent

def get_parse_error(code):
    """
    Checks code for ambiguous tabs or other basic parsing issues.

    :param code: a string containing a file's worth of Python code
    :returns: a string containing a description of the first parse error encountered,
              or None if the code is ok
    """
    # note that this uses non-public elements from stdlib's tabnanny, because tabnanny
    # is (very frustratingly) written only to be used as a script, but using it that way
    # in this context requires writing temporarily files, running subprocesses, blah blah blah
    code_buffer = StringIO(code)
    try:
        tabnanny.process_tokens(tokenize.generate_tokens(code_buffer.readline))
    except tokenize.TokenError, err:
        return "Could not parse code: %s" % err
    except IndentationError, err:
        return "Indentation error: %s" % err
    except tabnanny.NannyNag, err:
        return "Ambiguous tab at line %d; line is '%s'." % (err.get_lineno(), err.get_line())
    return None


def clean_diff_line_for_python_bug_2142(diff_line):
    if diff_line.endswith("\n"):
        return diff_line
    else:
        return diff_line + "\n\\ No newline at end of file\n"

def get_correct_indentation_diff(code, filename):
    """
    Generate a diff to make code correctly indented.

    :param code: a string containing a file's worth of Python code
    :param filename: the filename being considered (used in diff generation only)
    :returns: a unified diff to make code correctly indented, or
              None if code is already correctedly indented
    """
    code_buffer = StringIO(code)
    output_buffer = StringIO()
    reindenter = reindent.Reindenter(code_buffer)
    reindenter.run()
    reindenter.write(output_buffer)
    reindent_output = output_buffer.getvalue()
    output_buffer.close()
    if code != reindent_output:
        diff_generator = difflib.unified_diff(code.splitlines(True), reindent_output.splitlines(True),
                                              fromfile=filename, tofile=filename + " (reindented)")
        # work around http://bugs.python.org/issue2142
        diff_tuple = map(clean_diff_line_for_python_bug_2142, diff_generator)
        diff = "".join(diff_tuple)
        return diff
    else:
        return None

def is_merge():
    parent2 = os.environ.get("HG_PARENT2", None)
    return parent2 is not None and len(parent2) > 0

def parent_commit():
    parent1 = os.environ.get("HG_PARENT1", None)
    return parent1

class MercurialRuntimeError(Exception):
    pass

def run_mercurial_command(hg_command):
    try:
        hg_subprocess = Popen(hg_command.split(), stdout=PIPE, stderr=PIPE)
    except OSError:
        print >> sys.stderr, "Can't find the hg executable!"
        sys.exit(1)

    hg_out, hg_err = hg_subprocess.communicate()
    if len(hg_err) > 0:
        raise MercurialRuntimeError(hg_err)
    return hg_out

def parse_stdout_filelist(hg_out_filelist):
    files = hg_out_filelist.split()
    files = [f.strip(string.whitespace + "'") for f in files]
    files = filter(operator.truth, files) # get rid of empty entries
    return files

def changed_files():
    hg_out = run_mercurial_command("hg tip --template '{file_mods}'")
    return parse_stdout_filelist(hg_out)

def added_files():
    hg_out = run_mercurial_command("hg tip --template '{file_adds}'")
    return parse_stdout_filelist(hg_out)

def is_python_file(filename):
    return filename.endswith(".py")

def get_file_contents(filename, revision="tip"):
    hg_out = run_mercurial_command("hg cat -r %s %s" % (revision, filename))
    return hg_out

def save_commit_message(filename):
    commit_message = run_mercurial_command("hg tip --template '{desc}'")
    save_file = open(filename, "w")
    save_file.write(commit_message)
    save_file.close()

def save_diffs(diffs, filename):
    diff = "\n\n".join(diffs)
    diff_file = open(filename, "w")
    diff_file.write(diff)
    diff_file.close()

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Pretxncommit hook for Mercurial to check for whitespace issues")
    parser.add_argument("-n", "--no-indentation",
                        action="store_const",
                        default=False,
                        const=True,
                        help="don't check indentation, just basic parsing"
                       )
    parser.add_argument("-i", "--incremental",
                        action="store_const",
                        default=False,
                        const=True,
                        help="only check indentation if the file was previously correctly indented (or is new)"
                       )
    args = parser.parse_args(argv)

    if is_merge():
        # don't inspect merges: (a) they're complex and (b) they don't really introduce new code
        return 0

    block_commit = False

    diffs = []

    added_filenames = added_files()
    changed_filenames = changed_files()

    for filename in filter(is_python_file, added_filenames + changed_filenames):
        code = get_file_contents(filename)
        parse_error = get_parse_error(code)
        if parse_error is not None:
            print >> sys.stderr, "*** %s has parse error: %s" % (filename, parse_error)
            block_commit = True
        else:
            # parsing succeeded, it is safe to check indentation
            if not args.no_indentation:
                if args.incremental and filename in changed_filenames:
                    # only check it if it was clean before
                    old_file_contents = get_file_contents(filename, revision=parent_commit())
                    check_indentation = get_correct_indentation_diff(old_file_contents, "") is None
                else:
                    check_indentation = True
                if check_indentation:
                    indentation_diff = get_correct_indentation_diff(code, filename)
                    if indentation_diff is not None:
                        block_commit = True
                        diffs.append(indentation_diff)
                        print >> sys.stderr, "%s is not correctly indented" % filename

    if len(diffs) > 0:
        diffs_filename = ".hg/indentation_fixes.patch"
        save_diffs(diffs, diffs_filename)
        print >> sys.stderr, "*** To fix all indentation issues, run: cd `hg root` && patch -p0 < %s" % diffs_filename


    if block_commit:
        save_filename = ".hg/commit_message.saved"
        save_commit_message(save_filename)
        print >> sys.stderr, "*** Commit message saved to %s" % save_filename

    return int(block_commit)


if __name__ == '__main__':
    sys.exit(main())
