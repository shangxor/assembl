from __future__ import print_function
import os

from setuptools import setup, find_packages
try:
    from pip._internal.req import parse_requirements
    from pip._internal.download import PipSession
except ImportError:
    from pip.req import parse_requirements
    from pip.download import PipSession


here = os.path.abspath(os.path.dirname(__file__))
version = open(os.path.join(here, 'VERSION')).read().strip()
README = open(os.path.join(here, 'README.md')).read()
CHANGES = open(os.path.join(here, 'CHANGES.txt')).read()


def parse_reqs(req_files, links=False):
    """returns a list of requirements from a list of req files"""
    requirements = set()
    session = PipSession()
    for req_file in req_files:
        # parse_requirements() returns generator of pip.req.InstallRequirement objects
        parsed = parse_requirements(req_file, session=session)
        requirements.update({str(ir.req) if not links else ir.link.url.replace('git+', '')
                             for ir in parsed
                             if ir.link or not links})
    return list(requirements)


setup(name='assembl',
      version=version,
      description='Collective Intelligence platform',
      long_description=README + '\n\n' + CHANGES,
      classifiers=[
          "Programming Language :: Python :: 2.7",
          "Programming Language :: JavaScript",
          "Framework :: Pyramid",
          "Topic :: Communications",
          "Topic :: Internet :: WWW/HTTP",
          "Topic :: Internet :: WWW/HTTP :: Dynamic Content :: Message Boards",
          "Topic :: Internet :: WWW/HTTP :: WSGI :: Application",
          "License :: OSI Approved :: GNU Affero General Public License v3",
      ],
      author='',
      author_email='',
      url='http://assembl.org/',
      license='AGPLv3',
      keywords='web wsgi pyramid',
      # find_packages misses alembic somehow.
      packages=find_packages() + ['assembl.alembic', 'assembl.alembic.versions'],
      # scripts=['fabfile.py'],
      package_data={
          'assembl': [
              'locale/*/LC_MESSAGES/*.json',
              'locale/*/LC_MESSAGES/*.mo',
              'static/js/build/*.js',
              'static/js/build/*.map',
              'static*/img/*',
              'static*/img/*/*',
              'static*/img/*/*/*',
              'static/css/fonts/*',
              'static/css/themes/default/*css',
              'static/css/themes/default/img/*',
              'static/js/app/utils/browser-detect.js',
              'static/js/bower/*/dist/css/*.css',
              'static/js/bower/*/dist/img/*',
              'static/js/bower/*/css/*.css',
              'static/js/bower/*/*.css',
              # Missing: Widgets
              'static2/build/*.map',
              'static2/build/*.js',
              'static2/build/*.css',
              'static2/translations/*.json',
              'static2/fonts/*',
              'view_def/*.json',
              'configs/*.rc',
              'configs/*.ini',
              'templates/*.jinja2',
              'templates/*/*.jinja2',
              'templates/*/*/*.jinja2',
              'templates/*/*.tmpl',
              'nlp/data/*',
              'nlp/data/stopwords/*',
          ]
      },
      zip_safe=False,
      test_suite='assembl',
      setup_requires=['pip>=6'],
      install_requires=parse_reqs(['requirements.in', 'requirements-chrouter.in']),
      tests_require=parse_reqs(['requirements-tests.in']),
      dependency_links=parse_reqs(
          ['requirements.in', 'requirements-chrouter.in'],
          links=True
      ),
      extras_require={
          'docs': parse_reqs(['requirements-doc.in']),
          'dev': parse_reqs(['requirements-dev.in']),
          'test': parse_reqs(['requirements-tests.in']),
      },
      entry_points={
          "console_scripts": [
              "assembl-check-availability = assembl.scripts.check_availability:main",
              "assembl-db-manage = assembl.scripts.db_manage:main",
              "assembl-ini-files = assembl.scripts.ini_files:main",
              "assembl-imap-test = assembl.scripts.imap_test:main",
              "assembl-add-user  = assembl.scripts.add_user:main",
              "assembl-pypsql  = assembl.scripts.pypsql:main",
              "assembl-pshell  = assembl.scripts.pshell:main",
              "assembl-pserve   = assembl.scripts.pserve:main",
              "assembl-reindex-all-contents  = assembl.scripts.reindex_all_contents:main",
              "assembl-graphql-schema-json = assembl.scripts.export_graphql_schema:main"
          ],
          "paste.app_factory": [
              "main = assembl:main",
              "maintenance = assembl.maintenance:main"
          ],
      }
      )
