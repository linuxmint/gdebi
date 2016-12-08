
DOMAIN=gdebi
DESKTOP_IN_FILES := $(wildcard *.desktop.in)
DESKTOP_FILES := $(patsubst %.desktop.in,%.desktop,$(wildcard *.desktop.in))

all: $(DESKTOP_FILES)

%.desktop: %.desktop.in ../po/$(DOMAIN).pot
	intltool-merge -d ../po $< $@
