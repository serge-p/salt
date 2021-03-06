# -*- coding: utf-8 -*-
'''
Runner to manage Windows software repo
'''

# Import python libs
from __future__ import absolute_import, print_function
import os

# Import third party libs
import salt.ext.six as six
try:
    import msgpack
except ImportError:
    import msgpack_pure as msgpack  # pylint: disable=import-error

# Import salt libs
from salt.exceptions import CommandExecutionError, SaltRenderError
import salt.utils
import salt.utils.gitfs
import logging
import salt.minion
import salt.loader
import salt.template

log = logging.getLogger(__name__)

PER_REMOTE_PARAMS = ('ssl_verify',)


def genrepo(opts=None, fire_event=True):
    '''
    Generate winrepo_cachefile based on sls files in the winrepo_dir

    opts
        Specify an alternate opts dict. Should not be used unless this function
        is imported into an execution module.

    fire_event : True
        Fire an event on failure. Only supported on the master.

    CLI Example:

    .. code-block:: bash

        salt-run winrepo.genrepo
    '''
    if opts is None:
        opts = __opts__

    if 'win_repo' in opts:
        salt.utils.warn_until(
            'Nitrogen',
            'The \'win_repo\' config option is deprecated, please use '
            '\'winrepo_dir\' instead.'
        )
        winrepo_dir = opts['win_repo']
    else:
        winrepo_dir = opts['winrepo_dir']

    if 'win_repo_mastercachefile' in opts:
        salt.utils.warn_until(
            'Nitrogen',
            'The \'win_repo_mastercachefile\' config option is deprecated, '
            'please use \'winrepo_cachefile\' instead.'
        )
        winrepo_cachefile = opts['win_repo_mastercachefile']
    else:
        winrepo_cachefile = opts['winrepo_cachefile']

    ret = {}
    if not os.path.exists(winrepo_dir):
        os.makedirs(winrepo_dir)
    renderers = salt.loader.render(opts, __salt__)
    for root, _, files in os.walk(winrepo_dir):
        for name in files:
            if name.endswith('.sls'):
                try:
                    config = salt.template.compile_template(
                            os.path.join(root, name),
                            renderers,
                            opts['renderer'])
                except SaltRenderError as exc:
                    log.debug(
                        'Failed to render {0}.'.format(
                            os.path.join(root, name)
                        )
                    )
                    log.debug('Error: {0}.'.format(exc))
                    continue
                if config:
                    revmap = {}
                    for pkgname, versions in six.iteritems(config):
                        log.debug(
                            'Compiling winrepo data for package \'{0}\''
                            .format(pkgname)
                        )
                        for version, repodata in six.iteritems(versions):
                            log.debug(
                                'Compiling winrepo data for {0} version {1}'
                                .format(pkgname, version)
                            )
                            if not isinstance(version, six.string_types):
                                config[pkgname][str(version)] = \
                                    config[pkgname].pop(version)
                            if not isinstance(repodata, dict):
                                log.debug(
                                    'Failed to compile {0}.'.format(
                                        os.path.join(root, name)
                                    )
                                )
                                if fire_event:
                                    msg = 'Failed to compile {0}.'.format(
                                        os.path.join(root, name)
                                    )
                                    try:
                                        __jid_event__.fire_event(
                                            {'error': msg},
                                            'progress'
                                        )
                                    except NameError:
                                        log.error(
                                            'Attempted to fire the an event '
                                            'with the following error, but '
                                            'event firing is not supported: '
                                            '{0}'.format(msg)
                                        )
                                continue
                            revmap[repodata['full_name']] = pkgname
                    ret.setdefault('repo', {}).update(config)
                    ret.setdefault('name_map', {}).update(revmap)
    with salt.utils.fopen(
            os.path.join(winrepo_dir, winrepo_cachefile), 'w+b') as repo:
        repo.write(msgpack.dumps(ret))
    return ret


def update_git_repos(opts=None, masterless=False):
    '''
    Checkout git repos containing Windows Software Package Definitions

    opts
        Specify an alternate opts dict. Should not be used unless this function
        is imported into an execution module.


    CLI Example:

    .. code-block:: bash

        salt-run winrepo.update_git_repos
    '''
    if opts is None:
        opts = __opts__

    if 'win_repo' in opts:
        salt.utils.warn_until(
            'Nitrogen',
            'The \'win_repo\' config option is deprecated, please use '
            '\'winrepo_dir\' instead.'
        )
        winrepo_dir = opts['win_repo']
    else:
        winrepo_dir = opts['winrepo_dir']

    if 'win_gitrepos' in opts:
        salt.utils.warn_until(
            'Nitrogen',
            'The \'win_gitrepos\' config option is deprecated, please use '
            '\'winrepo_remotes\' instead.'
        )
        winrepo_remotes = opts['win_gitrepos']
    else:
        winrepo_remotes = opts['winrepo_remotes']

    if not any((salt.utils.gitfs.HAS_GITPYTHON, salt.utils.gitfs.HAS_PYGIT2)):
        # Use legacy code
        if not salt.utils.is_windows():
            # Don't warn on Windows, because Windows can't do cool things like
            # use pygit2. It has to fall back to git.latest.
            salt.utils.warn_until(
                'Nitrogen',
                'winrepo git support now requires either GitPython or pygit2. '
                'Please install either GitPython >= {0} (or pygit2 >= {1} with '
                'libgit2 >= {2}), clear out the winrepo_dir ({3}), and '
                'restart the salt-master service.'.format(
                    salt.utils.gitfs.GITPYTHON_MINVER,
                    salt.utils.gitfs.PYGIT2_MINVER,
                    salt.utils.gitfs.LIBGIT2_MINVER,
                    winrepo_dir
                )
            )
        ret = {}
        for remote_info in winrepo_remotes:
            if '/' in remote_info:
                targetname = remote_info.split('/')[-1]
            else:
                targetname = remote_info
            rev = None
            # If a revision is specified, use it.
            try:
                rev, remote_url = remote_info.strip().split()
            except ValueError:
                remote_url = remote_info
            gittarget = os.path.join(winrepo_dir, targetname).replace('.', '_')
            if masterless:
                result = __salt__['state.single']('git.latest',
                                                  name=remote_url,
                                                  rev=rev,
                                                  branch='winrepo',
                                                  target=gittarget,
                                                  force_checkout=True,
                                                  force_reset=True)
                if isinstance(result, list):
                    # Errors were detected
                    raise CommandExecutionError(
                        'Failed up update winrepo_remotes: {0}'.format(
                            '\n'.join(result)
                        )
                    )
                if 'name' not in result:
                    # Highstate output dict, the results are actually nested
                    # one level down.
                    key = next(iter(result))
                    result = result[key]
            else:
                mminion = salt.minion.MasterMinion(opts)
                result = mminion.states['git.latest'](remote_url,
                                                      rev=rev,
                                                      branch='winrepo',
                                                      target=gittarget,
                                                      force_checkout=True,
                                                      force_reset=True)
            ret[result['name']] = result['result']
        return ret
    else:
        # New winrepo code utilizing salt.utils.gitfs
        try:
            winrepo = salt.utils.gitfs.WinRepo(opts)
            winrepo.init_remotes(winrepo_remotes, PER_REMOTE_PARAMS)
            winrepo.fetch_remotes()
            winrepo.checkout()
        except Exception as exc:
            msg = 'Failed to update winrepo_remotes: {0}'.format(exc)
            log.error(msg, exc_info_on_loglevel=logging.DEBUG)
            return msg
        return winrepo.winrepo_dirs
