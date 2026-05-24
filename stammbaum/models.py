from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse


class Person(models.Model):
    GESCHLECHT_CHOICES = [('M', 'Männlich'), ('F', 'Weiblich'), ('U', 'Unbekannt')]

    elke_nr = models.IntegerField(unique=True, null=True, blank=True)
    nachname = models.CharField(max_length=200)
    vornamen = models.CharField(max_length=200, blank=True)
    rufname = models.CharField(max_length=100, blank=True)
    titel = models.CharField(max_length=100, blank=True)
    geschlecht = models.CharField(max_length=1, choices=GESCHLECHT_CHOICES, default='U')
    geburtsdatum = models.DateField(null=True, blank=True)
    geburtsort = models.CharField(max_length=200, blank=True)
    sterbedatum = models.DateField(null=True, blank=True)
    sterbeort = models.CharField(max_length=200, blank=True)
    geburtsname = models.CharField(max_length=200, blank=True, verbose_name='Geburtsname (Mädchenname)')
    konfession = models.CharField(max_length=100, blank=True)
    beruf = models.CharField(max_length=200, blank=True)
    todesursache = models.CharField(max_length=200, blank=True)
    anmerkungen = models.TextField(blank=True)
    foto = models.ImageField(upload_to='fotos/', null=True, blank=True)
    erstellt_am = models.DateTimeField(auto_now_add=True)
    geaendert_am = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nachname', 'vornamen']
        verbose_name = 'Person'
        verbose_name_plural = 'Personen'

    def __str__(self):
        parts = [self.nachname]
        if self.vornamen:
            parts.append(self.vornamen)
        if self.titel:
            parts.append(self.titel)
        return ', '.join([self.nachname, self.vornamen]) if self.vornamen else self.nachname

    def get_absolute_url(self):
        return reverse('person_detail', args=[self.pk])

    @property
    def vollname(self):
        parts = []
        if self.vornamen:
            parts.append(self.vornamen)
        parts.append(self.nachname)
        if self.titel:
            parts.append(self.titel)
        return ' '.join(parts)

    @property
    def geburtsjahr(self):
        return self.geburtsdatum.year if self.geburtsdatum else None

    @property
    def sterbejahr(self):
        return self.sterbedatum.year if self.sterbedatum else None

    @property
    def lebensdaten(self):
        if self.geburtsjahr and self.sterbejahr:
            return f"* {self.geburtsjahr} † {self.sterbejahr}"
        elif self.geburtsjahr:
            return f"* {self.geburtsjahr}"
        elif self.sterbejahr:
            return f"† {self.sterbejahr}"
        return ""

    def get_kinder(self):
        return Person.objects.filter(
            models.Q(elternschaft_kind__vater=self) | models.Q(elternschaft_kind__mutter=self)
        ).distinct()

    def get_eltern(self):
        try:
            elternschaft = self.elternschaft_kind
            return elternschaft.vater, elternschaft.mutter
        except Elternschaft.DoesNotExist:
            return None, None

    def get_geschwister(self):
        try:
            elternschaft = self.elternschaft_kind
            qs = Person.objects.filter(
                elternschaft_kind__vater=elternschaft.vater,
                elternschaft_kind__mutter=elternschaft.mutter,
            ).exclude(pk=self.pk)
            if elternschaft.vater:
                qs = qs | Person.objects.filter(elternschaft_kind__vater=elternschaft.vater).exclude(pk=self.pk)
            if elternschaft.mutter:
                qs = qs | Person.objects.filter(elternschaft_kind__mutter=elternschaft.mutter).exclude(pk=self.pk)
            return qs.distinct()
        except Elternschaft.DoesNotExist:
            return Person.objects.none()

    def get_partner(self):
        from django.db.models import Q
        ehen = Ehe.objects.filter(Q(partner1=self) | Q(partner2=self))
        partner_ids = []
        for ehe in ehen:
            partner_ids.append(ehe.partner2_id if ehe.partner1_id == self.pk else ehe.partner1_id)
        return Person.objects.filter(pk__in=partner_ids), ehen


class Ehe(models.Model):
    partner1 = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='ehen_als_partner1')
    partner2 = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='ehen_als_partner2')
    heiratsdatum = models.DateField(null=True, blank=True)
    heiratsort = models.CharField(max_length=200, blank=True)
    scheidungsdatum = models.DateField(null=True, blank=True)
    anmerkungen = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Ehe'
        verbose_name_plural = 'Ehen'

    def __str__(self):
        return f"{self.partner1} ∞ {self.partner2}"


class Elternschaft(models.Model):
    kind = models.OneToOneField(Person, on_delete=models.CASCADE, related_name='elternschaft_kind')
    vater = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name='kinder_als_vater')
    mutter = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name='kinder_als_mutter')

    class Meta:
        verbose_name = 'Elternschaft'
        verbose_name_plural = 'Elternschaften'

    def __str__(self):
        return f"Eltern von {self.kind}"


class Ort(models.Model):
    name = models.CharField(max_length=300, unique=True)
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    geocodiert_am = models.DateTimeField(null=True, blank=True)
    geo_problem = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Ort'
        verbose_name_plural = 'Orte'

    def __str__(self):
        return self.name


class Quelle(models.Model):
    TYP_CHOICES = [
        ('kirchenbuch', 'Kirchenbuch'),
        ('standesamt', 'Standesamt'),
        ('ahnenpass', 'Ahnenpass'),
        ('zeuge', 'Zeuge'),
        ('sonstiges', 'Sonstiges'),
    ]
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='quellen')
    beschreibung = models.CharField(max_length=500)
    typ = models.CharField(max_length=20, choices=TYP_CHOICES, default='sonstiges')
    erstellt_am = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Quelle'
        verbose_name_plural = 'Quellen'

    def __str__(self):
        return f"{self.get_typ_display()}: {self.beschreibung}"


class Aenderungslog(models.Model):
    AKTION_CHOICES = [('erstellt', 'Erstellt'), ('geaendert', 'Geändert'), ('geloescht', 'Gelöscht')]
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='aenderungen')
    benutzer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    aktion = models.CharField(max_length=20, choices=AKTION_CHOICES)
    felder = models.JSONField(default=dict)
    zeitstempel = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Änderungslog'
        verbose_name_plural = 'Änderungslogs'
        ordering = ['-zeitstempel']

    def __str__(self):
        return f"{self.aktion} {self.person} von {self.benutzer} am {self.zeitstempel:%d.%m.%Y}"


class Foto(models.Model):
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='fotos')
    bild = models.ImageField(upload_to='fotos/')
    beschriftung = models.CharField(max_length=300, blank=True)
    aufnahmejahr = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'Foto'
        verbose_name_plural = 'Fotos'

    def __str__(self):
        return f"Foto von {self.person}"
