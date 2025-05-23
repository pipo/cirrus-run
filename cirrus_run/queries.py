'''
Predefined queries for Cirrus API

Designed to work with the following schema (2022-01-17):
https://github.com/cirruslabs/cirrus-ci-web/blob/97d6ac7dbddb42aaa9736c4caab79cf361540f2a/schema.gql
'''


from time import monotonic as time, sleep
import logging

from . import CirrusAPI


log = logging.getLogger(__name__)


class CirrusQueryError(ValueError):
    '''Raised when query executes successfully but returns invalid data'''


class CirrusBuildError(RuntimeError):
    '''Raised on build failures'''

class CirrusCreditsError(RuntimeError):
    '''Raised when build fails due to lack of CI credits'''

class CirrusTimeoutError(RuntimeError):
    '''Raised when build takes too long'''


def get_repo(api: CirrusAPI, owner: str, repo: str) -> str:
    '''Get internal ID for GitHub repo'''
    query = '''
        query GetRepo($owner: String!, $repo: String!) {
            ownerRepository(platform: "github", owner: $owner, name: $repo) {
                id
                name
            }
        }
    '''
    params = dict(owner=owner, repo=repo)
    reply = api(query, params)
    if reply['ownerRepository']:
        return reply['ownerRepository']['id']
    raise CirrusQueryError('repo not found: {}/{}'.format(owner, repo))


def create_build(api: CirrusAPI,
                 repo_id: str,
                 repo_branch: str = 'master',
                 config: str = '') -> str:
    '''
    Trigger new build on Cirrus CI

    Return build ID
    '''
    query = '''
        mutation ScheduleCustomBuild($config: String!,
                                     $repo: ID!,
                                     $branch: String!,
                                     $mutation_id: String!) {
            createBuild(
                input: {
                    repositoryId: $repo,
                    branch: $branch,
                    clientMutationId: $mutation_id,
                    configOverride: $config
                }
            ) {
                build {
                    id
                    status
                }
            }
        }
    '''
    mutation_id = 'cirrus-run job {}'.format(int(time()))
    answer = api(
        query=query,
        params=dict(
            repo=repo_id,
            branch=repo_branch,
            mutation_id=mutation_id,
            config=config),
    )
    return answer['createBuild']['build']['id']


def wait_build(api, build_id: str, delay=3, abort=60*60, credits_error_message=None):
    '''Wait until build finishes'''
    ERROR_CONFIRM_TIMES = 3

    query = '''
        query GetBuild($build: ID!) {
            build(id: $build) {
                status
                tasks {
                    notifications {
                        message
                    }
                }
            }
        }
    '''
    params = dict(build=build_id)

    errors_confirmed = 0
    time_start = time()
    while time() < time_start + abort:
        response = api(query, params)
        status = response['build']['status']
        log.info('build https://cirrus-ci.com/build/{}: {}'.format(build_id, status))
        if status in {'COMPLETED'}:
            return True
        if status in {'CREATED', 'TRIGGERED', 'EXECUTING'}:
            errors_confirmed = 0
            sleep(delay)
            continue
        if status in {'NEEDS_APPROVAL', 'FAILED', 'ABORTED', 'ERRORED'}:
            errors_confirmed += 1
            if errors_confirmed < ERROR_CONFIRM_TIMES:
                sleep(2 * delay / (ERROR_CONFIRM_TIMES - 1))
                continue
            else:
                if credits_error_message is not None:
                    for task in response['build']['tasks']:
                        for notif in task['notifications']:
                            if credits_error_message in notif['message']:
                                raise CirrusCreditsError('build {} ran out of CI credits'.format(build_id))

                raise CirrusBuildError('build {} was terminated: {}'.format(build_id, status))
        raise ValueError('build {} returned unknown status: {}'.format(build_id, status))
    raise CirrusTimeoutError('build {} timed out'.format(build_id))


def build_log(api, build_id):
    '''Yield build log in chunks of text'''
    query = '''
        query GetBuildLog($build: ID!) {
            build(id: $build) {
                tasks {
                    id
                    name
                    commands {
                        name
                    }
                }
            }
        }
    '''
    params = dict(build=build_id)
    url_template = 'https://api.cirrus-ci.com/v1/task/{task[id]}/logs/{command[name]}.log'
    response = api(query, params)
    for task in response['build']['tasks']:
        yield '\n## Task: {task[name]}'.format(**locals())
        for command in task['commands']:
            yield '\n## Task instruction: {command[name]}'.format(**locals())
            url = url_template.format(**locals())
            log = api.get(url)
            if log.status_code == 200:
                yield log.text
            else:
                yield 'Unable to fetch url: {}'.format(url)
