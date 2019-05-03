import os
import sys
import re
import json
from hashlib import sha256
from os.path import join, normpath
from contextlib import nested

from semantic_version import Version

from .common import venv, task, exists, is_integration_env, fill_template, configure_github_user
from .sudoer import upgrade_yarn, install_build_dependencies, install_node_and_yarn, clear_aptitude_cache


def get_node_base_path(c):
    return normpath(join(
        c.projectpath, 'assembl', 'static', 'js'))


def get_new_node_base_path(c):
    return normpath(join(
        c.projectpath, 'assembl', 'static2'))


def get_node_modules_path(c):
    return normpath(join(
        get_node_base_path(c), 'node_modules'))


def get_new_node_modules_path():
    return normpath(join(
        get_new_node_base_path(), 'node_modules'))


@task()
def install_bower(c):
    with c.cd(get_node_base_path(c)):
        with venv(c):
            c.run('npm install bower po2json requirejs')


@task()
def update_bower(c):
    with c.cd(get_node_base_path(c)):
        with venv(c):
            c.run('npm update bower po2json')


def get_node_bin_path(c):
    return normpath(join(
        get_node_modules_path(c), '.bin'))


def bower_cmd(c, cmd, relative_path='.'):
    with c.cd(c.config.projectpath):
        bower_cmd = normpath(join(get_node_bin_path(c), 'bower'))
        po2json_cmd = normpath(join(get_node_bin_path(c), 'po2json'))
        if not exists(c, bower_cmd) or not exists(c, po2json_cmd):
            print 'Bower not present, installing ...'
            install_bower()
        with c.cd(relative_path):
            with venv(c):
                c.run(' '.join(("node", bower_cmd, '--allow-root', cmd)))


def _bower_foreach_do(c, cmd):
    bower_cmd(c, cmd)
    bower_cmd(c, cmd, 'assembl/static/widget/card')
    bower_cmd(c, cmd, 'assembl/static/widget/session')
    bower_cmd(c, cmd, 'assembl/static/widget/video')
    bower_cmd(c, cmd, 'assembl/static/widget/vote')
    bower_cmd(c, cmd, 'assembl/static/widget/creativity')
    bower_cmd(c, cmd, 'assembl/static/widget/share')


def separate_pip_install(context, package, wrapper=None):
    cmd = '%s/bin/pip install' % context.config.projectpath + "/venv"
    if wrapper:
        cmd = wrapper % (cmd,)
    context_dict = dict(context)
    cmd = cmd % context_dict
    cmd = "egrep '^%(package)s' %(projectpath)s/requirements-prod.frozen.txt | sed -e 's/#.*//' | xargs %(cmd)s" % dict(
        cmd=cmd, package=package, **context_dict)
    context.run(cmd)


@task()
def update_bower_requirements(c, force_reinstall=False):
    """ Normally not called manually """
    _bower_foreach_do(c, 'prune')
    if force_reinstall:
        _bower_foreach_do(c, 'install --force')
    else:
        _bower_foreach_do(c, 'update')


@task()
def update_node(c, force_reinstall=False):
    """
    Install node and npm to latest version specified. This is done inside of a virtual environment.
    """
    n_version = c.config._internal.node.version
    npm_version = c.config._internal.node.npm
    node_version_cmd_regex = re.compile(r'v' + n_version.replace('.', r'\.'))
    with venv(c, True):
        node_version_cmd_result = c.run('node --version', echo=True).stdout
    match = node_version_cmd_regex.match(str(node_version_cmd_result))
    if not match or force_reinstall:
        # Stop gulp and webpack because otherwise node may be busy
        # TODO: Implement supervisor_process_stop
        # supervisor_process_stop('dev:gulp')
        # supervisor_process_stop('dev:webpack')
        with venv(c, True):
            c.run("rm -rf venv/lib/node_modules/")
            c.run("rm -f venv/bin/npm")  # remove the symlink first otherwise next command raises OSError: [Errno 17] File exists
            c.run("nodeenv --node=%s --npm=%s --python-virtualenv assembl/static/js" % (n_version, npm_version))
        upgrade_yarn(c)
        with c.cd(get_node_base_path(c)):
            with venv(c):
                c.run("npm install reinstall -g")

        update_npm_requirements(force_reinstall=True)
    else:
        print "Node version OK"


@task()
def update_npm_requirements(c, install=False, force_reinstall=False):
    """Normally not called manually"""
    with c.cd(get_node_base_path(c)):
        if install:
            with venv(c):
                c.run('yarn')
        elif force_reinstall:
            with venv(c):
                c.run('reinstall')
        else:
            with venv(c):
                c.run('npm update')

    yarn_path = c.run('which yarn')
    static2_path = get_new_node_base_path(c)
    with c.cd(static2_path):
        if exists(c, yarn_path):
            if install or force_reinstall:
                print('Removing node_modules directory...')
                with venv(c):
                    c.run('rm -rf {}'.format(os.path.join(static2_path, 'node_modules')))
            with venv(c):
                c.run(yarn_path)
        else:
            if install:
                with venv(c):
                    c.run('npm install')
            elif force_reinstall:
                with venv(c):
                    c.run('reinstall')
            else:
                with venv(c):
                    c.run('npm update')


@task()
def clone_repository(c, reset=False):
    """
    Clone repository
    """
    print('Cloning Git repository')

    # Remove dir if necessary
    path = c.config.projectpath
    if exists(c, os.path.join(path, ".git")):
        print "%s/.git already exists" % path
        if reset:
            c.run("rm -rf %s/.git" % path)
        else:
            return

    # Clone
    branch = c.config.get('gitbranch', 'master')
    repo = c.config.get('gitrepo', 'https://github.com/assembl/assembl.git')
    with c.cd(path):
        c.run("git clone --branch {0} {1} {2}".format(
            branch, repo, os.path.join(path, 'assembl')))


@task()
def updatemaincode(c):
    """
    Update code and/or switch branch
    """
    print('Updating Git repository')
    with c.cd(c.config.projectpath):
        c.run('git fetch')
        c.run('git checkout %s' % c.config._internal.gitbranch)
        c.run('git pull %s %s' % (c.config._internal.gitrepo, c.config._internal.gitbranch))


@task()
def update_pip_requirements(c, force_reinstall=False):
    """
    Update external dependencies on remote host.
    """
    with venv(c):
        c.run('pip install -U setuptools "pip<10" ')

    if force_reinstall:
        c.run("%s/bin/pip install --ignore-installed -r %s/requirements.txt" % (c.config.venvpath, c.config.projectpath))
    else:
        specials = [
            # setuptools and lxml need to be installed before compiling dm.xmlsec.binding
            ("lxml", None),
            # Thanks to https://github.com/pypa/pip/issues/4453 disable wheel separately.
            ("dm.xmlsec.binding", "%s --install-option='-q'"),
            ("pycurl", None),
        ]
        for package, wrapper in specials:
            separate_pip_install(c, package, wrapper)
        cmd = "%s/bin/pip install -r %s/requirements.txt" % (c.config.venvpath, c.config.projectpath)
        c.run("yes w | %s" % cmd)


@task()
def app_update_dependencies(c, force_reinstall=False):
    """
    Updates all python and javascript dependencies.  Everything that requires a
    network connection to update.
    """
    update_pip_requirements(c, force_reinstall=force_reinstall)
    update_node(c, force_reinstall=force_reinstall)
    update_bower(c)
    update_bower_requirements(c, force_reinstall=force_reinstall)
    update_npm_requirements(c, force_reinstall=force_reinstall)


@task()
def compile_stylesheets(c):
    """
    Generate *.css files from *.scss
    """
    project_path = os.getenv('CI_PROJECT_DIR', c.config.projectpath)
    with c.cd(project_path):
        with nested(c.cd('assembl/static/js'), venv(c)):
            c.run('./node_modules/.bin/gulp sass')
        with venv(c):
            # no-qa
            c.run('./assembl/static/js/node_modules/.bin/node-sass --source-map ' +
                  '-r -o assembl/static/widget/card/app/css --source-map assembl/static/widget/card/app/css assembl/static/widget/card/app/scss')
        with venv(c):
            c.run('./assembl/static/js/node_modules/.bin/node-sass --source-map ' +
                  '-r -o assembl/static/widget/video/app/css --source-map assembl/static/widget/video/app/css assembl/static/widget/video/app/scss')
        with venv(c):
            c.run('./assembl/static/js/node_modules/.bin/node-sass --source-map ' +
                  '-r -o assembl/static/widget/session/css --source-map assembl/static/widget/session/css assembl/static/widget/session/scss')


@task()
def compile_messages(c):
    """
    Run compile *.mo file from *.po
    """
    with venv(c, True):
        c.run('python2 setup.py compile_catalog')
    with venv(c, True):
        c.run("python2 assembl/scripts/po2json.py")


@task()
def compile_javascript(c):
    """
    Generates and minifies javascript
    """
    project_path = os.getenv('CI_PROJECT_DIR', c.config.projectpath)
    with c.cd(project_path):
        with c.cd('assembl/static/js'):
            with venv(c):
                c.run('./node_modules/.bin/gulp libs')
            with venv(c):
                c.run('./node_modules/.bin/gulp browserify:prod')
        if c.config.wsginame != 'dev.wsgi':
            with c.cd('assembl/static2'):
                with venv(c):
                    c.run('yarn run build')


@task()
def compile_static_assets(c):
    """Separated mostly for tests, which need to run alembic manually"""
    compile_stylesheets(c)
    compile_messages(c)
    compile_javascript(c)


@task()
def create_wheelhouse(c, dependency_links=None):
    if not dependency_links:
        dependency_links = c.run('grep "git+http" %(here)s/requirements-dev.frozen.txt > %(here)s/deps.txt' % {
                                 'here': c.config.code_root}).stdout
    tmp_wheel_path = os.path.join(c.config.code_root, 'wheelhouse')
    cmd = 'pip wheel --wheel-dir=%s --process-dependency-links -r %s' % (tmp_wheel_path, dependency_links)
    if is_integration_env(c):
        c.run(cmd)
    else:
        with venv(c, True):
            c.run(cmd)


def create_wheel_name(version, num=0, commit_hash=None, branch=None, tag=None):
    """
    Follows the recommended naming scheme of PEP 491 (https://www.python.org/dev/peps/pep-0491/#file-name-convention)
    """
    if not num:
        # CI condition of a tag (which should only ever be put on master)
        long_version = version
    elif branch:
        long_version = '%s.dev%d+%s' % (version, num, branch)
    elif tag:
        long_version = '%s.dev%d+%s' % (version, num, tag.split('-').join('_'))
    else:
        long_version = '%s.dev%d+%s' % (version, num, commit_hash)

    return "assembl-{version}-py{python_major}-none-any.whl".format(
        version=long_version,
        python_major=sys.version_info.major)


def git_version_data(c):
    # Take the latest HEAD
    branch = c.run('git symbolic-ref --short HEAD').stdout.strip()
    latest_tag_desc = c.run('git describe').stdout.strip()
    annotated_tag_desc = c.run('git describe --tags HEAD').stdout.strip()
    parts = re.match(r"([\d\.]+)(-(\d+)-g([0-9a-f]+))?$", annotated_tag_desc)
    # Git flow uses annotated tags based on the branch names for hotfixes, will break this.
    # TODO: Add loop to go back in time to find the other tags
    assert parts, "annotated tag %s is not a version tag" % (annotated_tag_desc.split('-')[0])
    if not parts.group(2):
        version = annotated_tag_desc
        commit_tag = None
        commit_hash = c.run('git rev-parse --short HEAD').stdout.strip()
        num = 0
    else:
        # The commit is not an official tagged version
        (version_tag, _, num, commit_hash) = parts.groups()
        num = int(num)
        version = str(Version(version_tag).next_patch())
        if not re.match(r".+-\d+-g[0-9a-f]+$", latest_tag_desc):
            commit_tag = latest_tag_desc.rsplit('-', 2)[0]
        else:
            commit_tag = None
    return (version, num, commit_hash, commit_tag, branch)


def update_wheels_json_data(c, json_data):
    (version, num, commit_hash, commit_tag, branch) = git_version_data(c)
    base_name = create_wheel_name(version, num, commit_hash=commit_hash)
    if commit_tag and commit_tag not in json_data:
        json_data[commit_tag] = {
            'sha1': commit_hash,
            'link_name': create_wheel_name(version, num, commit_hash, tag=commit_tag),
            'wheel_name': base_name
        }
    # update the links for the branches
    json_data[branch] = {
        'sha1': commit_hash,
        'link_name': create_wheel_name(version, num, commit_hash, branch=branch),
        'wheel_name': base_name
    }
    return json_data


@task()
def create_wheel(c, house=None):
    tmp_wheel_path = house if house else os.path.join(c.config.code_root, 'wheelhouse')
    (version, num, commit_hash, commit_tag, branch) = git_version_data(c)
    c.run("python setup.py bdist_wheel -d " + tmp_wheel_path)


def write_update_json_data(c, json_filepath):
    if exists(c, json_filepath):
        with open(json_filepath) as fp:
            json_data = json.load(fp)
    else:
        json_data = {}
    json_data = update_wheels_json_data(c, json_data)
    with open(json_filepath, 'w+') as fp:
        json.dump(json_data, fp)
    return json_data


@task()
def push_wheelhouse(c, house=None):
    """
    Push dependency links wheelhouse to either:
    A) an S3 bucket
    B) a remote folder via SSH
    C) a local folder
    """
    tmp_wheel_path = house if house else os.path.join(c.config.code_root, 'wheelhouse')
    wheel_path = c.config.wheelhouse
    json_filename = 'special-wheels.json'
    if not wheel_path:
        raise RuntimeError("No wheelhouse location was defined in configuration. Cannot continue...")
    elif not tmp_wheel_path:
        raise RuntimeError("There is no local wheelhouse to push. Quitting...")

    if wheel_path.strip().startswith('s3://'):
        # S3
        try:
            import botocore
            import boto3
        except ImportError:
            raise RuntimeError("AWS CLI and Boto3 have to be installed to push to s3. Quitting...")

        s3 = boto3.resource('s3')
        bucket_name = wheel_path[5:]
        bucket = s3.Bucket(bucket_name)
        exists = True
        try:
            s3.meta.client.head_bucket(Bucket=bucket_name)
        except botocore.exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                exists = False
        if not exists:
            raise RuntimeError("The Bluenove wheelhouse bucket does not exist on S3. Please create it before continuing...")

        # There must exist a JSON file which holds links to the latest wheels on various branches, along with version tags
        json_filepath = os.path.join(tmp_wheel_path, json_filename)
        try:
            bucket.download_file(json_filename, json_filepath)
        except:
            pass

        indexable_names = {json_filename, 'index.html'}
        wheel_hashes = {}
        existing_wheels = set()
        old_links = set()
        for filename in os.listdir(tmp_wheel_path):
            indexable_names.add(filename)
            with open(os.path.join(tmp_wheel_path, filename), 'rb') as f:
                m = sha256()
                m.update(f.read())
            wheel_hashes[filename] = m.hexdigest()
        for summary in bucket.objects.all():
            if summary.key in (json_filename, 'error.html'):
                # Don't add the JSON file to the indexable list
                continue
            if summary.size == 0:
                old_links.add(summary.key)
                continue
            existing_wheels.add(summary.key)
            if summary.key not in wheel_hashes:
                indexable_names.add(summary.key)
                wheel_hashes[summary.key] = ''  # temporary for testing

        json_data = write_update_json_data(c, json_filepath)

        # put empty objects for S3-redirections (https://docs.aws.amazon.com/AmazonS3/latest/dev/how-to-page-redirect.html)
        for value in json_data.itervalues():
            name = value['link_name']
            if name in old_links:
                old_links.remove(name)
            bucket.put_object(Key=name, ACL='public-read', Body='',
                              WebsiteRedirectLocation='/%s' % value['wheel_name'])
            indexable_names.add(name)

        if old_links:
            bucket.delete_objects(Delete={"Objects": [{"Key": l} for l in old_links]})
        indexable_names = list(indexable_names)
        indexable_names.sort()

        output = os.path.join(tmp_wheel_path, 'index.html')
        fill_template(c, 'wheelhouse_index.jinja2', output, {'wheelhouse': indexable_names, 'hashes': wheel_hashes})
        with open(output) as fp:
            bucket.put_object(Body=fp, Key='index.html', ContentType='text/html', ACL='public-read')

        with open(json_filepath) as fp:
            bucket.put_object(Body=fp, Key=json_filename, ContentType='application/json', ACL='public-read')

        for file in os.listdir(tmp_wheel_path):
            if file not in existing_wheels:
                with open(os.path.join(tmp_wheel_path, file)) as fp:
                    bucket.put_object(Body=fp, Key=file, ACL='public-read')

    elif wheel_path.strip().startswith('local://'):
        c.run('cp -r %s %s' % (tmp_wheel_path, wheel_path.split('local://')[1]))
    else:
        c.run('cp -r %s %s' % (tmp_wheel_path, wheel_path))


@task()
def push_built_themes_to_remote_bucket(c):
    """
    Push webpack built themes CSS + JS files of themes into respective S3 bucket.
    Expects boto3, zip, to be pre-installed.


    Output:
        - Two S3 buckets with a folder structure matching the build folder of both gulp and webpack, respectively
        - a compressed and uncompressed versions of js + css files locally and on S3 bucket
    """
    import boto3
    s3 = boto3.resource('s3')
    v2_build_path = os.path.join(c.config.code_root, 'assembl/static2/build/themes')
    v1_build_path = os.path.join(c.config.code_root, 'assembl/static/build/')
    v2_bucket_name = 'bluenove-client-themes'
    v1_bucket_name = 'bluenove-deprecated-client-themes'
    buckets = ((1, v1_bucket_name), (2, v2_bucket_name))

    def determine_content_type(path):
        ext = os.path.splitext(path)[1]
        if ext:
            if re.match(r'.*js$', ext):
                return 'text/javascript'
            elif re.match(r'.*css$', ext):
                return 'text/css'
        return 'application/octet-stream'

    def determine_content_encoding(path):
        basename, _ = os.path.splitext(path)
        if 'compressed_' in basename:
            return 'gzip'
        return None

    def compress_file(path):
        # Only compress css + js files
        base, ext = os.path.splitext(path)
        basename = os.path.basename(base)
        zip_file = os.path.join(os.path.dirname(path), 'compressed_' + basename + ext)
        if ext:
            if re.match(r'.*js$', ext) or re.match(r'.*css$', ext):
                c.run('gzip -vkc -9 %s > %s' % (path, zip_file))
        else:
            print("No files of JS/CSS type to compress")

    for theme_version, bucket_name in buckets:
        if theme_version == 1:
            theme_path = v1_build_path
        else:
            theme_path = v2_build_path
        # Push all content to S3, even if the files exist on S3, reupload them
        for root, dirs, files in os.walk(theme_path):
            for filename in files:
                local_path = os.path.join(root, filename)
                s3_path = os.path.relpath(local_path, theme_path)
                configs = {
                    'ACL': 'public-read',
                    'ContentType': determine_content_type(local_path),
                    'CacheControl': 'max-age=3600',
                    'ContentEncoding': determine_content_encoding(local_path)
                }
                s3.meta.client.upload_file(local_path, bucket_name, s3_path, configs)


@task(
    install_build_dependencies,
    install_node_and_yarn,
    configure_github_user,
    clear_aptitude_cache)
def prepare_cicd_build(c):
    """
    There is full assumption of being in CI/CD environment when calling this function
    """
    project_path = os.gettenv('CI_PROJECT_DIR', c.config.code_root)
    with c.cd(os.path.join(project_path, 'assembl/static/css/themes/vendor')):
        c.run('git clone git@github.com:bluenove/assembl-client-themes.git')

    with c.cd(os.path.join(project_path, 'assembl/static2/css/themes/vendor')):
        c.run('git clone git@github.com:bluenove/assembl2-client-themes.git')

    # Build JS dependencies for V1 and V2
    install_bower(c)
    update_bower_requirements(c, force_reinstall=True)
    update_npm_requirements(c, force_reinstall=True)


