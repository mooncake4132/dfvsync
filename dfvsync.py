#!/usr/bin/env python3
from collections import namedtuple
import configparser
import enum
import json
import logging
import os
import pkg_resources
import re
import shlex
import sys
import urllib.request


Release = namedtuple('Release', 'version name url tag archive_url')
DockerBuild = namedtuple('DockerBuild', 'version tag status')

logger = logging.getLogger(__name__)


def setup_logger(level=logging.DEBUG):
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('[%(asctime)s][%(levelname)s][%(name)s] %(message)s'))
    logger.addHandler(handler)


def get_version(tag):
    m = re.search('[\sv](\d+(?:\.\d+)*)', tag)
    if not m:
        logger.warning('Cannot identify version from tag {!r}.'.format(tag))
        return None

    return m.group(1)


def load_configs(config_file, encoding='utf-8'):
    config = configparser.ConfigParser()

    # We need ConfigParser to be case-sensitive.
    config.optionxform = str

    config.read(config_file, encoding)
    return config


def files_version(files, encoding='utf-8'):
    version = None

    for filename, regex in files.items():
        with open(filename, encoding=encoding) as fp:
            m = re.search(regex, fp.read())
            if not m:
                raise ValueError('Failed to match {!r} in file {}.'.format(regex, filename))

            file_version = m.group(1)
            if version and version != file_version:
                raise ValueError(
                    'Found two different version from the files in the repo: {} != {}.'
                    .format(version, file_version))

            version = file_version

    return version


def update_files_version(files, version, encoding='utf-8'):
    for filename, regex in files.items():
        with open(filename, encoding=encoding) as fp:
            contents = fp.read()
            m = re.search(regex, contents)
            if not m:
                raise ValueError('Failed to match {!r} in file {}.'.format(regex, filename))

            s, t = m.span(1)
            contents = contents[:s] + version + contents[t:]

        with open(filename, 'w', encoding=encoding) as fp:
            fp.write(contents)


def create_version_commit(service_name, files, version, *, git_user_email=None, git_user_name=None):
    for filename in files:
        os.system('git add "{}"'.format(shlex.quote(filename)))

    if git_user_email:
        os.system('git config user.email "{}"'.format(shlex.quote(git_user_email)))

    if git_user_name:
        os.system('git config user.name "{}"'.format(shlex.quote(git_user_name)))

    os.system('git commit -m "Bumped {} version to {}"'.format(
        shlex.quote(service_name), shlex.quote(version)))
    os.system('git tag -a "v{0}" -m "Release for version {0}"'.format(shlex.quote(version)))
    os.system('git push')


class HttpClient:
    def __init__(self, *, use_https=True):
        self.headers = {'User-Agent': 'mooncake4132/dfvsync'}

    def get_text(self, url, encoding='utf-8'):
        req = urllib.request.Request(url, method='GET')
        response = urllib.request.urlopen(req, timeout=10)
        return response.read().decode(encoding)

    def get_json(self, url):
        return json.loads(self.get_text(url))


class GithubRepo:
    NAME = 'Github'
    RELEASES_URL = 'https://api.github.com/repos/{}/{}/releases'

    def __init__(self, http, username, repo_name):
        self.http = http
        self.username = username
        self.repo_name = repo_name

    def __str__(self):
        return self.NAME

    def __repr__(self):
        return '<{} {}/{}>'.format(self.__class__.__name__, self.username, self.repo_name)

    @property
    def releases_url(self):
        return self.RELEASES_URL.format(self.username, self.repo_name)

    def get_releases(self, top=5):
        releases = {}

        releases_dict = self.http.get_json(self.releases_url)
        for release_dict in releases_dict[:top]:
            version = get_version(release_dict['tag_name'])
            if not version:
                continue

            releases[version] = Release(
                version=version,
                name=release_dict['name'],
                url=release_dict['url'],
                tag=release_dict['tag_name'],
                archive_url=release_dict['tarball_url'])

        return sorted(releases.values(), key=lambda r: r.version)


class DockerhubRepo:
    NAME = 'Dockerhub'
    BUILDS_URL = 'https://hub.docker.com/v2/repositories/{}/{}/buildhistory/'
    IGNORE_TAGS = {'latest'}

    def __init__(self, http, username, repo_name):
        self.http = http
        self.username = username
        self.repo_name = repo_name

    def __str__(self):
        return self.NAME

    def __repr__(self):
        return '<{} {}/{}>'.format(self.__class__.__name__, self.username, self.repo_name)

    @property
    def builds_url(self):
        return self.BUILDS_URL.format(self.username, self.repo_name)

    def get_builds(self):
        builds = {}

        builds_dict = self.http.get_json(self.builds_url)['results']
        for builds_dict in builds_dict:
            if builds_dict['dockertag_name'] in self.IGNORE_TAGS:
                continue

            version = get_version(builds_dict['dockertag_name'])
            if not version:
                continue

            # Ignore builds that failed
            if builds_dict['status'] < 0:
                continue

            builds[build.version] = DockerBuild(
                version=version, tag=release_dict['dockertag_name'], status=builds_dict['status'])

        return sorted(builds.values(), key=lambda r: r.version)


DEFAULT_CONFIG_FILE = '.dfvsync.cfg'


def main():
    config = load_configs(DEFAULT_CONFIG_FILE)
    setup_logger()
    logger.info('Loaded config values from {!r}.'.format(DEFAULT_CONFIG_FILE))

    http = HttpClient()

    source_provider = config['source']['provider']
    if source_provider.lower() == GithubRepo.NAME.lower():
        source_repo = GithubRepo(http, config['source']['username'], config['source']['repo_name'])
    else:
        raise ValueError('Unknown source provider {!r}.'.format(source_provider))

    releases = source_repo.get_releases()
    logger.info('Identified {} versioned releases from {}.'.format(len(releases), source_repo))
    logger.debug(releases)

    docker_provider = config['docker']['provider']
    if docker_provider.lower() == DockerhubRepo.NAME.lower():
        docker_repo = DockerhubRepo(http, config['docker']['username'], config['docker']['repo_name'])
    else:
        raise ValueError('Unknown docker provider {!r}.'.format(docker_provider))

    builds = docker_repo.get_builds()
    logger.info('Identified {} versioned builds from {}.'.format(len(builds), docker_repo))
    logger.debug(builds)

    dockerfile_version = files_version(config['files'])
    logger.info('Dockerfile repo is using version {}.'.format(dockerfile_version))

    # Ignore versions lower than what we have.
    min_version = pkg_resources.parse_version(dockerfile_version)
    releases = [release for release in releases if pkg_resources.parse_version(release.version) > min_version]

    # Ensure we don't already have builds for these release versions
    build_versions = {build.version for build in builds}
    releases = [release for release in releases if release.version not in build_versions]

    if not releases:
        logger.info('Docker builds are already up-to-date.')
        return

    logger.info('Creating docker builds for these versions: {}'.format(', '.join(r.version for r in releases)))
    for release in releases:
        update_files_version(config['files'], release.version)
        create_version_commit(
            config['source']['repo_name'], config['files'], release.version,
            git_user_email=config['git']['user_email'], git_user_name=config['git']['user_name'])

if __name__ == '__main__':
    main()
