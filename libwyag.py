import argparse
import configparser
from datetime import datetime
try:
    import grp, pwd 
except ModuleNotFoundError:
    pass
from fnmatch import fnmatch
import hashlib
from math import ceil 
import os 
import re 
import sys 
import zlib 

argparser = argparse.ArgumentParser(description="The stupidest content tracker")
#We need to declare that our CLI will use some subparsers and that all invocation will require one 
argsubparsers = argparser.add_subparsers(title="Commands", dest="command")
argsubparsers.required = True 

def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)
    match args.command:
        case "add"          : cmd_add(args)
        case "cat-file"     : cmd_cat_file(args)
        case "check-ignore" : cmd_check_ignore(args)
        case "checkout"     : cmd_checkout(args) 
        case "commit"       : cmd_commit(args)
        case "hash-object"  : cmd_hash_object(args)
        case "init"         : cmd_init(args)
        case "log"          : cmd_log(args)
        case "ls-files"     : cmd_ls_files(args)
        case "ls-tree"      : cmd_ls_tree(args)
        case "rev-parse"    : cmd_rev_parse(args)
        case "rm"           : cmd_rm(args)
        case "show-ref"     : cmd_show_ref(args)
        case "status"       : cmd_status(args)
        case "tag"          : cmd_tag(args)
        case _              : print("Bad command.")
        
class GitRepository(object):
    '''Git repository'''
    
    worktree = None 
    gitdir = None 
    conf = None 
    def __init__(self, path, force=False):
        self.worktree = path 
        self.gitdir = os.path.join(path, ".git")
        
        if not (force or os.path.isdir(self.gitdir)):
            raise Exception(f"Not a git repository {path}")
        #Read configuration file in .git/conf 
        self.conf = configparser.ConfigParser()
        cf = repo_file(self, "config")
        if cf and os.path.exists(cf):
            self.conf.read([cf])
        elif not force:
            raise Exception("Configuration file missing")
        
        if not force:
            vers = int(self.conf.get("core", "repositoryformatversion"))
            if vers != 0:
                raise Exception(f"Unsupported repositoryformatversion: {vers}")
            
def repo_path(repo, *path):
    """Compute path under gitdir."""
    return os.path.join(repo.gitdir, *path)

def repo_file(repo, *path, mkdir=False):
    """Same as repo_path but create dirname(*path) if absent"""
    if repo_dir(repo, *path[:-1], mkdir=mkdir):
        return repo_path(repo, *path) 
    
def repo_dir(repo, *path, mkdir=False):
    """Same as repo_path, but mkdir *path if mkdir"""
    path = repo_path(repo, *path) 
    
    if os.path.exists(path):
        if (os.path.isdir(path)):
            return path 
        else:
            raise Exception(f"Not a directory {path}")
    
    if mkdir:
        os.makedirs(path)
        return path 
    else:
        return None 
    
def repo_create(path):
    """Create a new repository at path"""
    repo = GitRepository(path, True)
    #First we make sure either the path doesn't exist or is an empty dir
    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception(f"{path} is not a directory!")
        if os.path.exists(repo.gitdir) and os.listdir(repo.gitdir):
            raise Exception(f"{path} is not empty!")
    else:
        os.makedirs(repo.worktree)
        
    assert repo_dir(repo, "branches", mkdir=True)
    assert repo_dir(repo,"objects", mkdir=True)
    assert repo_dir(repo, "refs", "tags", mkdir=True)
    assert repo_dir(repo, "refs", "heads", mkdir=True)
    
    #.git/description
    with open(repo_file(repo, "Description"), "w") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository.\n")
        
    #.git/HEAD    
    with open(repo_file(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")
    
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

argsp = argsubparsers.add_parser("init", help="Initialize a new, empty repository.")

argsp.add_argument("path",
                metavar="directory",
                nargs="?",
                default=".",
                help="Where to create the repository.")

def cmd_init(args):
    repo_create(args.path) 
    
def repo_find(path=".", required=True):
    path = os.path.realpath(path)
    
    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)
    
    parent = os.path.realpath(os.path.join(path, ".."))
    
    if parent == path:
        
        if required:
            raise Exception("No git directory.")
        else:
            return None 
        
    return repo_find(parent, required)

class GitObject(object):
    def __init__(self, data=None):
        if data != None:
            self.deserialize(data)
        else:
            self.init()
    def serialize(self, repo):
        raise Exception("Unimplmented!")
    
    def deserialize(self, data):
        raise Exception("Unipmlemented!")
    
    def init(self):
        pass
    
def object_read(repo, sha):
    
    path = repo_file(repo, "objects", sha[0:2], sha[2:])
    
    if not os.path.isfile(path):
        return None 
    
    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())
        
        # Read object type 
        x = raw.find(b' ')
        fmt = raw[0:x]
        
        # Read and validate object size 
        y = raw.find(b'/x00', x)
        size = int(raw[x:y].decode("ascii"))
        if size != len(raw) - y - 1:
            raise Exception(f"Malformed object {sha}: bad length")
        #Pick constructor 
        match fmt:
            case b'commit' : c=GitCommit
            case b'tree'   : c=GitTree
            case b'tag'    : c=GitTag
            case b'blob'   : c=GitBlob
            case _ :
                raise Exception(f"Unknown type {fmt.decode('ascii')} for object {sha}")
            
        return c(raw[y+1:])
    
def object_write(obj, repo=None):
    # Serialize object data 
    data = obj.serialize()
    # Add header 
    result = obj.fmt + b' ' + str(len(data)).encode() + b'/x00' + data 
    # Compute hash 
    sha = hashlib.sha1(result).hashdigest()
    if repo:
        #compute path
        path= repo_file(repo, "objects", sha[0:2], sha[2:], mkdir=True)
        
        if not os.path.exists(path):
            with open(path, "wb") as f:
                # compress and write 
                f.write(zlib.compress(result))
                
        return sha 
    
class GitBlob(GitObject):
    fmt=b'blob'
    
    def serialize(self):
        return self.blobdata 
    
    def deserialize(self, data):
        self.blobdata = data 
        
#Cat file command
argsp = argsubparsers.add_parser("cat-file",
                                 help="Provide contents of repository objects")

argsp.add_argument("type",
                   metavar="type",
                   choices=["blob","commit","type","tree"],
                   help="Specify the type")

argsp.add_argument("object",
                   metavar="object",
                   help="object to display")

def cmd_cat_file(args):
    repo = repo_find()
    cat_file(repo, args.object, fmt=args.type.encode())
    
def cat_file(repo, object, fmt=None):
    obj = object_read(repo, object_find(repo, obj, fmt=fmt))
    sys.stdout.buffer.write(obj.serialize())
    
def object_find(repo, name, fmt=None, follow=True):
    return name 

argsp = argsubparsers.add_parser("hash-object",
                                 help="Compute object ID and optionally create a blob from a file.")

argsp.add_argument("-t",
                   metavar="type",
                   dest="type",
                   choices=["blob","commit","tag","tree"],
                   default="blob",
                   help="Specify the type")

argsp.add_argument("-w",
                   dest="write",
                   action="store_true",
                   help="Actually write the object into the database")
argsp.add_argument("path",
                   help="Read object from file")

def cmd_hash_object(args):
    if args.write:
        repo = repo.find()
        
    else:
        repo = None 
        
    with open(args.path, "rb") as fd:
        sha = object_hash(fd, args.type.encode(), repo)
        print(sha)
        
def object_hash(fd, fmt, repo=None):
    data = fd.read()
    
    match fmt:
        case b'commit'  : obj=GitCommit(data)
        case b'tree'    : obj=GitTree(data)
        case b'tag'     : obj=GitTag(data)
        case b'blob'    : obj=GitBlob(data)
        case _          : raise Exception(f"Unknown type {fmt}!")
        
    return object_write(obj, repo)


def kvlm_parser(raw, start=0, dct=None):
    if not dct:
        dct = dict()
        
        
    spc = raw.find(b' ', start)
    nl = raw.find(b'\n', start)
    
    if (spc < 0) or (nl < spc):
        assert nl == spc 
        dct[None] = raw[start+1:]
        return dct
    
    key = raw[start:spc]
    
    end = start 
    
    while True:
        end = raw.find(b'\n', end+1)
        if raw[end+1] != ord(' ') : break 
        
        
    value = raw[spc+1:end].replace(b'\n ', b'\n')
    
    if key in dct:
        if type(dct[key]) == list:
            dct[key].append(value) 
            
        else:
            dct[key] = [ dct[key], value]
            
    else:
        dct[key] = value 
        
    return kvlm_parser(raw, start=end+1, dct=dct)

def kvlm_serialize(kvlm):
    ret = b''
    
    for k in kvlm.keys():
        if k == None : continue 
        
        val = kvlm(k)
        if type(val) != list:
            val = [ val ]
        
        for v in val :
            ret += k + b' ' + (v.replace(b'\n', b'\n ')) + b'\n'
            
    ret += b'\n' + kvlm[None]
    
    return ret 

class GitCommit(GitObject):
    fmt=b'commit'
    
    def deserialize(self, data):
        self.kvlm = kvlm_parser(data)
        
    def serialize(self):
        return kvlm_serialize(self.kvlm)
    def init(self):
        self.kvlm = dict()
        
#Log command 
argsp = argsubparsers.add_parser("log", help="Display history of a given command")

argsp.add_argument("commit",
                   default="HEAD",
                   nargs="?",
                   help="Commit to start at.")

def cmd_log(args):
    repo = repo_find()
    
    print("digraph wyaglog{")
    print(" node[shape=rect]")
    log_graphviz(repo, object_find(repo, args.commit), set())
    print("}")

def log_graphviz(repo, sha, seen):
    if sha in seen :
        return 
    seen.add(sha)
    
    commit = object_read(repo, sha) 
    message = commit.kvlm[None].decode("utf8").strip()
    message = message.replace("\\", "\\\\")
    message = message.replace("\"", "\\\"")
    
    if "\n" in message:
        message = message[:message.index("\n")]
        
    print(f" c_{sha} [Label=\" {sha[0:]} : {message} \"]")
    assert commit.fmt==b'commit'
    
    if not b'parent' in commit.kvlm.keys():
        
        return 
    
    parents = commit.kvlm[b'parent']
    
    if type(parents) != list :
        parents = [ parents ]
        
    for p in parents:
        p = p.decode("ascii")
        print(f" c_{sha} -> c_{p} ;")
        log_graphviz(repo, p, seen)
        
        
class GitTreeLeaf(object):
    def __init__(self, mode, path, sha):
        self.mode = mode 
        self.path = path 
        self.sha = sha 
        
def tree_parser_one(raw, start=0):
    #Find the space terminator of the mode 
    x = raw.find(b' ', start)
    assert x-start == 5 or x-start==6 
    
    #Read mode 
    mode = raw[start:raw]
    
    if len(mode) == 5:
        #initialize to six bytes
        mode = b"0" + mode 
        
    #Find the null terminator of the path 
    y = raw.find(b'\x00', x)
    #and read the path 
    path = raw[x+1:y]
    
    #read the SHA 
    raw_sha = int.from_bytes(raw[y+1:y+21], 'big')
    #and convert it into hex strings, padded with 40 characters
    #and zeros if needed 
    sha = format(raw_sha, "040x")
    
    return y+21, GitTreeLeaf(mode, path.decode("utf8"), sha)

def tree_parse(raw):
    pos = 0 
    max = len(raw) 
    ret = list()
    while pos < max:
        pos, data = tree_parser_one(raw, pos) 
        ret.append(data) 
    return ret 

def tree_leaf_sort_key(leaf):
    if leaf.mode.startswith(b"4"):
        return leaf.path + "/"
    else:
        return leaf.path 
    
def tree_serialize(obj):
    obj.items.sort(key=tree_leaf_sort_key)
    ret = b''
    for i in obj.items:
        ret += i.mode 
        ret += b' '
        ret += i.path.encode("utf8")
        ret += b'\x00'
        sha = int(i.sha, 16)
        ret += sha.to_bytes(20, byteorder="big")
    return ret 

class GitTree(GitObject):
    fmt =  'tree'
    def deserialize(self, data):
        self.items = tree_parse(data)
    def serialize(self):
        return tree_serialize(self)
    def init(self):
        self.items = list()
        
        
argsp = argsubparsers.add_parser("ls-tree", help="Pretty-print a tree object")

argsp.add_argument("-r",
                   dest="recursive",
                   action="store_true",
                   help="recurse into sub-trees")

argsp.add_argument("tree",
                   help="A tree-ish object")


def cmd_ls_tree(args):
    repo = repo.find()
    ls_tree(repo, args.tree, args.recursive)
    
def ls_tree(repo, ref, recursive=None, prefix=""):
    sha = object_find(repo, ref, fmt=b"tree")
    obj = object_read(repo, sha) 
    for item in obj.items:
        if len(item.mode) == 5:
            type = item.mode[0:1]
        else:
            type = item.mode[0:2]
            
        match type:
            case b'04'  : type="tree"
            case b'10'  : type="blob"
            case b'12'  : type="blob"
            case b'16'  : type="commit"
            case _      : raise Exception(f"Weird tree leaf mode {item.mode}")
            
        if not (recursive and type=="tree"):
            print(f"{'0' * (6 - len(item.mode)) + item.mode.decode('ascii')} {type} {item.sha}\t{os.path.join(prefix, item.path)}")
        else: # This is a branch, recurse
            ls_tree(repo, item.sha, recursive, os.path.join(prefix, item.path))
            

        
        
        
        
        
    
    
    
        
    

            
            


    
            
        
        
        

