import os
import sys
import multiprocessing
import signal
import urllib.parse
from pakrat import util, log, repo, repos, progress

__version__ = '0.3.2'

def sync(basedir=None, objrepos=[], repodirs=[], repofiles=[],
         repoversion=None, delete=False, combined=False, callback=None):
    """ Mirror repositories with configuration data from multiple sources.

    Handles all input validation and higher-level logic before passing control
    on to threads for doing the actual syncing. One thread is created per
    repository to alleviate the impact of slow mirrors on faster ones.
    """
    util.validate_repos(objrepos)
    util.validate_repofiles(repofiles)
    util.validate_repodirs(repodirs)

    if not basedir:
        basedir = os.getcwd()  # default current working directory

    util.validate_basedir(basedir)

    if repoversion:
        delete = False  # versioned repos have nothing to delete

    for _file in repofiles:
        objrepos += repos.from_file(_file)

    for _dir in repodirs:
        objrepos += repos.from_dir(_dir)

    prog = progress.Progress()  # callbacks talk to this object
    manager = multiprocessing.Manager()
    queue = manager.Queue()
    processes = []
    for objrepo in objrepos:
        prog.update(objrepo.id)  # Add the repo to the progress object
        yumcallback = progress.YumProgress(objrepo.id, queue, callback)
        repocallback = progress.ProgressCallback(queue, callback)
        dest = util.get_repo_dir(basedir, objrepo.id)
        p = multiprocessing.Process(target=repo.sync, args=(objrepo, dest,
                                    repoversion, delete, combined, yumcallback,
                                    repocallback))
        p.start()
        processes.append(p)

    def stop(*args):
        """ Inner method for terminating threads on signal events.

        This method uses os.kill() to send a SIGKILL directly to the process ID
        because the child processes are running blocking calls that will likely
        take a long time to complete.
        """
        log.error('Caught exit signal - aborting')
        while len(processes) > 0:
            for p in processes:
                os.kill(p.pid, signal.SIGKILL)
                if not p.is_alive():
                    processes.remove(p)
        sys.exit(1)  # safe to do exit() here because we are a worker

    # Catch user-cancelled or killed signals to terminate threads.
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    while len(processes) > 0:
        # If data is waiting in the queue from the workers, process it. This
        # needs to be done in the current scope so that one progress object may
        # hold all of the results. (This might be easier with Python 3's
        # nonlocal keyword).
        while not queue.empty():
            e = queue.get()
            if 'action' not in e:
                continue
            if e['action'] == 'repo_init' and 'value' in e:
                prog.update(e['repo_id'], set_total=e['value'])
            elif e['action'] == 'download_end' and 'value' in e:
                prog.update(e['repo_id'], pkgs_downloaded=e['value'])
            elif e['action'] == 'repo_metadata':
                prog.update(e['repo_id'], repo_metadata=e['value'])
            elif e['action'] == 'repo_complete':
                pass  # should already know this, but handle it anyways.
            elif e['action'] == 'repo_error':
                prog.update(e['repo_id'], repo_error=e['value'])
            elif e['action'] == 'local_pkg_exists':
                prog.update(e['repo_id'], pkgs_downloaded=1)
        for p in processes:
            if not p.is_alive():
                processes.remove(p)

    # Return tuple (#repos, #fail, elapsed time)
    return (len(objrepos), prog.totals['errors'], prog.elapsed())
