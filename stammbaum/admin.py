from django.contrib import admin
from .models import Person, Ehe, Elternschaft, Ort, Quelle, Aenderungslog, Foto


class ElternschaftInline(admin.StackedInline):
    model = Elternschaft
    fk_name = 'kind'
    extra = 0


class QuelleInline(admin.TabularInline):
    model = Quelle
    extra = 1


class FotoInline(admin.TabularInline):
    model = Foto
    extra = 0


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ['nachname', 'vornamen', 'titel', 'geschlecht', 'geburtsdatum', 'geburtsort', 'sterbedatum', 'elke_nr']
    list_filter = ['geschlecht', 'konfession']
    search_fields = ['nachname', 'vornamen', 'geburtsort', 'sterbeort']
    inlines = [ElternschaftInline, QuelleInline, FotoInline]
    fieldsets = [
        ('Name', {'fields': ['nachname', 'vornamen', 'rufname', 'titel', 'geschlecht']}),
        ('Geburt', {'fields': ['geburtsdatum', 'geburtsort']}),
        ('Tod', {'fields': ['sterbedatum', 'sterbeort', 'todesursache']}),
        ('Sonstiges', {'fields': ['konfession', 'beruf', 'anmerkungen', 'foto', 'elke_nr']}),
    ]


@admin.register(Ehe)
class EheAdmin(admin.ModelAdmin):
    list_display = ['partner1', 'partner2', 'heiratsdatum', 'heiratsort']
    search_fields = ['partner1__nachname', 'partner2__nachname']


@admin.register(Elternschaft)
class ElternschaftAdmin(admin.ModelAdmin):
    list_display = ['kind', 'vater', 'mutter']
    search_fields = ['kind__nachname', 'vater__nachname', 'mutter__nachname']


@admin.register(Ort)
class OrtAdmin(admin.ModelAdmin):
    list_display = ['name', 'lat', 'lon', 'geocodiert_am']
    search_fields = ['name']


@admin.register(Quelle)
class QuelleAdmin(admin.ModelAdmin):
    list_display = ['person', 'typ', 'beschreibung']
    list_filter = ['typ']


@admin.register(Aenderungslog)
class AenderungslogAdmin(admin.ModelAdmin):
    list_display = ['person', 'benutzer', 'aktion', 'zeitstempel']
    list_filter = ['aktion']
    readonly_fields = ['person', 'benutzer', 'aktion', 'felder', 'zeitstempel']
