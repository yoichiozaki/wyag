import argparse  # for parsing commandline arguments
import sys
import collections  # for OrderedDict
import configparser  # for parsing .ini config file
import hashlib  # for SHA-1
import os  # for handling paths
import re  # for regular expression
import zlib  # for compression

argparser = argparse.ArgumentParser(description="The stupid content tracker")

argsubparsers = argparser.add_subparsers(title="Commands", dest="command")
argsubparsers.required = True
argsp = argsubparsers.add_parser(
    "init", help="Initialize a new, empty repository.")
argsp.add_argument("path", metavar="directory", nargs="?",
                   default=".", help="Where to create the repository.")
argsp = argsubparsers.add_parser(
    "cat-file", help="Provide content of repository objects")
argsp.add_argument("type", metavar="type", choices=[
                   "blob", "commit", "tag", "tree"], help="Specify the type")
argsp.add_argument("object", metavar="object", help="The object to display")
argsp = argsubparsers.add_parser(
    "hash-object", help="Compute object ID and optionally creates a blob from a file")
argsp.add_argument("-t", metavar="type", dest="type", choice=[
                   "blob", "commit", "tag", "tree"], default="blob", help="Specify the type")
argsp.add_argument("-w", dest="write", action="store_true",
                   help="Actually write the object into the database")
argsp.add_argument("path", help="Read object from <file>")
argsp = argsubparsers.add_parser(
    "log", help="Display history of a given commit")
argsp.add_argument("commit", default="HEAD", nargs="?",
                   help="Commit to start at")
argsp = argsubparsers.add_parser("ls-tree", help="Pretty print a tree object")
argsp.add_argument("object", help="The object to show")


def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)

    if args.command == "add":
        cmd_add(args)
    elif args.command == "cat-file":
        cmd_cat_file(args)
    elif args.command == "checkout":
        cmd_checkout(args)
    elif args.command == "commit":
        cmd_commit(args)
    elif args.command == "hash-object":
        cmd_hash_object(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "log":
        cmd_log(args)
    elif args.command == "ls-tree":
        cmd_ls_tree(args)
    elif args.command == "merge":
        cmd_merge(args)
    elif args.command == "rebase":
        cmd_rebase(args)
    elif args.command == "rev-parse":
        cmd_rev_parse(args)
    elif args.command == "rm":
        cmd_rm(args)
    elif args.command == "show-ref":
        cmd_show_ref(args)
    elif args.command == "tag":
        cmd_tag(args)


class GitRepository(object):
    """A git repository"""

    worktree = None
    gitdir = None  # .git
    conf = None  # .git/config

    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")

        if not (force or os.path.isdir(self.gitdir)):
            raise Exception("Not a Git repository {}".format(path))

        self.conf = configparser.ConfigParser()
        cf = repo_file(self, "config")

        if cf and os.path.exists(cf):
            self.conf.read([cf])
        elif not force:
            raise Exception("Configuration file missing")

        if not force:
            # core.repositoryformatversion
            vers = int(self.conf.get("core", "repositoryformatversion"))
            if vers != 0:
                raise Exception(
                    "Unsupported repositoryformatversion {}".format(vers))


def repo_path(repo, *path):
    """Compute path under repo's gitdir"""
    return os.path.join(repo.gitdir, *path)


def repo_file(repo, *path, mkdir=False):
    """Same as repo_path, but create dirname(*path) if absent. For example, repo_file(r, \"refs\", \"remotes\", \" origin\". \"HEAD\") will create .git/refs/remotes/origin."""
    if repo_dir(repo, *path[:-1], mkdir=mkdir):
        return repo_path(repo, *path)


def repo_dir(repo, *path, mkdir=False):
    """Same as repo_path, but mkdir *path if absent if mkdir."""
    path = repo_path(repo, *path)

    if os.path.exists(path):
        if os.path.isdir(path):
            return path
        else:
            raise Exception("Not a directory {}".format(path))

    if mkdir:
        os.makedirs(path)
        return path
    else:
        return None


def repo_create(path):
    """Create a new repository at path"""
    repo = GitRepository(path, force=True)

    # First, we make sure that the path either does not exists or is an empty dir.
    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception("{} is not a directory!".format(path))
        if os.listdir(repo.worktree):
            raise Exception("{} is not empty".format(path))
    else:
        os.makedirs(repo.worktree)

    assert(repo_dir(repo, "branches", mkdir=True))  # .git/branches/
    assert(repo_dir(repo, "objects", mkdir=True))  # .git/objects/
    assert(repo_dir(repo, "refs", "tags", mkdir=True))  # .git/refs/tags/
    assert(repo_dir(repo, "refs", "heads", mkdir=True))  # .git/refs/heads/

    # .git/description
    with open(repo_file(repo, "description"), "w") as f:
        f.write(
            "Unnamed repository: edit this file 'description' to name the repository.\n")

    # .git/HEAD
    with open(repo_file(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/main\n")

    # .git/config
    with open(repo_file(repo, "config"), "w") as f:
        config = repo_default_config()
        config.write(f)

    return repo


def repo_default_config():
    ret = configparser.ConfigParser()

    ret.add_section("core")
    ret.set("core", "repositoryformatversion", "0")
    ret.set("core", "filemode", "false")
    ret.set("core", "bare", "false")

    return ret


def repo_find(path=".", required=True):
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)

    # If we have not returned, recurse in parent.
    parent = os.path.realpath(os.path.join(path, ".."))

    if parent == path:  # base case
        if required:
            raise Exception("No git directory.")
        else:
            return None

    return repo_find(parent, required=required)


class GitObject(object):
    repo = None

    def __init__(self, repo, data=None):
        self.repo = repo
        if data != None:
            self.deserialize(data)

    def serialize(self):
        """This function MUST be implemented by subclasses.
        It must read the object's contents from self.data, a byte string, and do whatever it takes to covert it into a meaningful representation. What exactly that means depends on each subclass."""
        raise Exception("Unimplemented!")

    def deserialize(self, data):
        raise Exception("Unimplemented!")


class GitBlob(GitObject):
    fmt = b'blob'

    def serialize(self):
        return self.blobdata

    def deserialize(self, data):
        self.blobdata = data


class GitCommit(GitObject):
    fmt = b'commit'

    def deserialize(self, data):
        self.kvlm = kvlm_parse(data)

    def serialize(self):
        return kvlm_serialize(self.kvml)


class GitTree(GitObject):
    fmt = b'tree'

    def deserialize(self, data):
        self.items = tree_parse(data)

    def serialize(self):
        return tree_serialize(self)


class GitTreeLeaf(object):
    def __init__(self, mode, path, sha):
        self.mode = mode
        self.path = path
        self.sha = sha


def object_read(repo, sha):
    """Read object sha from Git repository repo. Return a GitObject whose exact type depends on the object."""
    path = repo_file(repo, "objects", sha[0:2], sha[2:])

    # object format
    # +----------+--------------------------------------------------------------------+
    # | address  |                                                                    |
    # +----------+--------------------------------------------------------------------+
    # | 00000000 | 63 6f 6d 6d 69 74 20 31  30 38 36 00 74 72 65 65 | commit 1086.tree|
    # | 00000010 | 20 32 39 66 66 31 36 63  39 63 31 34 65 32 36 35 | 29ff16c9c14e265 |
    # | 00000020 | 32 62 32 32 66 38 62 37  38 62 62 30 38 61 35 61 | 2b22f8b78bb08a5a|
    # +----------+--------------------------------------------------------------------+

    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())

        x = raw.find(b' ')
        fmt = raw[0:x]  # object type

        y = raw.find(b'\x00', x)
        size = int(raw[x:y].decode("ascii"))
        if size != len(raw) - y - 1:
            raise Exception("Malformed object {}: bad length".format(sha))

        if fmt == b'commit':
            c = GitCommit
        elif fmt == b'tree':
            c = GitTree
        elif fmt == b'tag':
            c = GitTag
        elif fmt == b'blob':
            c = GitBlob
        else:
            raise Exception("Unknown type {} for object {}".format(
                fmt.decode("ascii"), sha))

        return c(repo, raw[y+1:])


def object_write(obj, actually_write=True):
    data = obj.serialize()
    result = obj.fmt + b' ' + str(len(data)).encode() + b'\x00' + data
    sha = hashlib.sha1(result).hexdigest()

    if actually_write:
        path = repo_file(obj.repo, "objects",
                         sha[0:2], sha[2:], mkdir=actually_write)

        with open(path, 'wb') as f:
            f.write(zlib.compress(result))

    return sha


def object_find(repo, name, fmt=None, follow=True):
    return name


def kvlm_parse(raw, start=0, dct=None):
    # Key-Value List with Message
    if not dct:
        dct = collections.OrderedDict()

    spc = raw.find(b' ', start)  # space
    nl = raw.find(b'\n', start)  # new line

    # if space appers before newline, there is a keyword

    # basecase
    # if newline appers first (or there is no space at all, in which case return -1), there is a blank line. A blank line means the remainder of the data is message.
    if spc < 0 or nl < spc:
        assert(nl == start)
        dct[b''] = raw[start+1:]  # '': message...
        return dct

    # read keyword
    key = raw[start:spc]

    # find the end of the value
    end = start
    while True:
        end = raw.find(b'\n', end + 1)
        if raw[end + 1] != ord(' '):
            break

    # read value
    value = raw[spc+1:end].replace(b'\n', b'\n')

    if key in dct:  # do not overwrite the existing data
        if type(dct[key]) == list:
            dct[key].append(value)
        else:
            dct[key] = [dct[key], value]
    else:
        dct[key] = value

    return kvlm_parse(raw, start=end+1, dct=dct)


def kvlm_serialize(kvlm):
    ret = b''

    for k in kvlm.keys():
        if k == b'':
            continue  # skip the message itself
        val = kvlm[k]
        if type(val) != list:
            val = [val]

        for v in val:
            ret += k + b' ' + (v.replace(b'\n', b'\n')) + b'\n'

    ret += b'\n' + kvlm[b'']  # append message

    return ret


def tree_parse(raw):
    pos = 0
    max = len(raw)
    ret = list()
    while pos < max:
        pos, data = tree_parse_one(raw, pos)
        ret.append(data)

    return ret


def tree_parse_one(raw, start=0):
    # format: [mode] space [path] 0x00 [sha-1]
    x = raw.find(b' ', start)
    assert(x - start == 5 or x - start == 6)

    mode = raw[start:x]

    y = raw.find(b'\x00', x)
    path = raw[x+1:y]

    sha = hex(int.from_bytes(raw[y+1:y+21], "big"))[2:]

    return y + 21, GitTreeLeaf(mode, path, sha)


def tree_serialize(obj):
    ret = b''
    for i in obj.items:
        ret += i.mode
        ret += b' '
        ret += i.path
        ret += b'\x00'
        sha = int(i.sha, 16)
        ret += sha.to_bytes(20, byteorder="big")
    return ret

#############################################################
# wyag init
# usage: wyag init <path>
#############################################################


def cmd_init(args):
    repo_create(args.path)

#############################################################
# wyag cat-file
# usage: wyag cat-file <type> <object>
#############################################################


def cmd_cat_file(args):
    repo = repo_find()
    cat_file(repo, args.object, fmt=args.type.encode())


def cat_file(repo, obj, fmt=None):
    obj = object_read(repo, object_find(repo, obj, fmt=fmt))
    sys.stdout.buffer.write(obj.serialize())

#############################################################
# wyag hash-object
# usage: wyag hash-object [-w] [-t <type>] <file>
#############################################################


def cmd_hash_object(args):
    if args.write:
        repo = GitRepository(".")
    else:
        repo = None

    with open(args.path, 'rb') as f:
        sha = object_hash(f, args.type.encode(), repo)
        print(sha)


def object_hash(f, fmt, repo=None):
    data = f.read()

    if fmt == b'commit':
        obj = GitCommit(repo, data)
    elif fmt == b'tree':
        obj = GitTree(repo, data)
    elif fmt == b'tag':
        obj = GitTag(repo, data)
    elif fmt == b'blob':
        obj = GitBlob(repo, data)
    else:
        raise Exception("Unknown type {}".format(fmt))

    return object_write(obj, repo)

#############################################################
# wyag log
# usage: wyag log <commit id>
#############################################################


def cmd_log(args):
    repo = repo_find()

    print("digraph wyaglog(")
    log_graphviz(repo, object_find(repo, args.commit), set())
    print(")")


def log_graphviz(repo, sha, seen):
    if sha in seen:
        return
    seen.add(sha)

    commit = object_read(repo, sha)
    assert(commit.fmt == b'commit')

    if not b'parent' in commit.kvlm.keys():
        return  # initial commit

    parents = commit.kvlm[b'parent']

    if type(parents) != list:
        parents = [parents]

    for p in parents:
        p = p.decode("ascii")
        print("c_{} -> c_{}".format(sha, p))
        log_graphviz(repo, p, seen)

#############################################################
# wyag ls-tree
# usage: wyag ls-tree <object id>
#############################################################


def cmd_ls_tree(args):
    repo = repo_find()
    obj = object_read(repo, object_find(repo, args.object, fmt=b'tree'))

    for item in obj.items:
        # <mode> <object type> <sha> \t <path>
        print("{} {} {}\t{}".format(
            "0" * (6 - len(item.mode)) + item.mode.decode("ascii"),
            object_read(repo, item.sha).fmt.decode("ascii"), item.sha,
            item.path.decode("ascii")))
