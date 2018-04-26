#!/usr/bin/env python3

import os
from setuptools import setup
from glob import glob
from re import compile

GETTEXT_NAME='gdebi'
I18NFILES = []

# Look/set what version we have
changelog = 'debian/changelog'
if os.path.exists(changelog):
    head=open(changelog).readline()
    match = compile('.*\((.*)\).*').match(head)
    if match:
        version = match.group(1)
        f=open('GDebi/Version.py', 'w')
        f.write('VERSION="%s"\n' % version)
        f.close()

for filepath in glob('po/mo/*/LC_MESSAGES/*.mo'):
    lang = filepath[len('po/mo/'):]
    targetpath = os.path.dirname(os.path.join('share/locale', lang))
    I18NFILES.append((targetpath, [filepath]))

s = setup(name='gdebi',
          version=version,
          test_suite="tests",
          packages=['GDebi'],
          scripts=['gdebi', 'gdebi-gtk', 'gdebi-kde'],
          data_files=[('share/gdebi/',
                       ['data/gdebi.ui', 'data/GDebiKDEDialog.ui',
                        'data/GDebiKDEInstallDialog.ui']),
                      ('share/applications',
                       ['build/gdebi.desktop']),
                      ('share/applications/kde4',
                       ['build/gdebi-kde.desktop']),
                      ('share/application-registry',
                       ['data/gdebi.applications']),
                      ('share/gdebi/',
                       ['data/gdebi.png']),
                      ('share/polkit-1/actions/',
                       ['data/com.ubuntu.pkexec.gdebi-gtk.policy'])] + I18NFILES)

# Make sure that the mo files are generated and up-to-date
if 'build' in s.commands:
    os.system('intltool-merge -d po data/gdebi.desktop.in'\
              ' build/gdebi.desktop')
    os.system('intltool-merge -d po data/gdebi-kde.desktop.in'\
              ' build/gdebi-kde.desktop')
    os.system('make -C po update-po')
