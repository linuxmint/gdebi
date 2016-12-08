
DOMAIN=gdebi
PO_FILES := $(wildcard *.po)
CONTACT=sebastian.heinlein@web.de

all: update-po

top_srcdir=../

# update the pot
$(DOMAIN).pot:
	XGETTEXT_ARGS=--msgid-bugs-address=$(CONTACT) intltool-update -p -g $(DOMAIN)

# merge the new stuff into the po files
merge-po: $(PO_FILES)
	XGETTEXT_ARGS=--msgid-bugs-address=$(CONTACT) intltool-update -r -g $(DOMAIN); 

# create mo from the pos
%.mo : %.po
	mkdir -p mo/$(subst .po,,$<)/LC_MESSAGES/ 
	msgfmt $< -o mo/$(subst .po,,$<)/LC_MESSAGES/$(DOMAIN).mo 

# dummy target 
update-po: $(DOMAIN).pot merge-po $(patsubst %.po,%.mo,$(wildcard *.po))
	# update the po files 
	#for f in $(PO_FILES); do \
	#	intltool-update -r $${f%.po}  -g $(DOMAIN); \
	#done

