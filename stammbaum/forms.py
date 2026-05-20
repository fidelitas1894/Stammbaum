from django import forms
from .models import Person, Ehe, Elternschaft, Quelle


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = [
            'nachname', 'vornamen', 'rufname', 'titel', 'geschlecht',
            'geburtsdatum', 'geburtsort', 'sterbedatum', 'sterbeort',
            'konfession', 'beruf', 'todesursache', 'anmerkungen', 'foto',
        ]
        widgets = {
            'geburtsdatum': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'sterbedatum': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'anmerkungen': forms.Textarea(attrs={'rows': 4}),
            'nachname': forms.TextInput(attrs={'class': 'form-control'}),
            'vornamen': forms.TextInput(attrs={'class': 'form-control'}),
            'rufname': forms.TextInput(attrs={'class': 'form-control'}),
            'titel': forms.TextInput(attrs={'class': 'form-control'}),
            'geschlecht': forms.Select(attrs={'class': 'form-select'}),
            'geburtsort': forms.TextInput(attrs={'class': 'form-control'}),
            'sterbeort': forms.TextInput(attrs={'class': 'form-control'}),
            'konfession': forms.TextInput(attrs={'class': 'form-control'}),
            'beruf': forms.TextInput(attrs={'class': 'form-control'}),
            'todesursache': forms.TextInput(attrs={'class': 'form-control'}),
            'anmerkungen': forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}),
        }


class ElternschaftForm(forms.ModelForm):
    class Meta:
        model = Elternschaft
        fields = ['vater', 'mutter']
        widgets = {
            'vater': forms.Select(attrs={'class': 'form-select'}),
            'mutter': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vater'].queryset = Person.objects.filter(geschlecht='M')
        self.fields['vater'].required = False
        self.fields['mutter'].queryset = Person.objects.filter(geschlecht='F')
        self.fields['mutter'].required = False
        self.fields['vater'].empty_label = '--- Kein Vater bekannt ---'
        self.fields['mutter'].empty_label = '--- Keine Mutter bekannt ---'


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True

    def value_from_datadict(self, data, files, name):
        return files.getlist(name)


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={
            'accept': '.ELK,.GM3,.elk,.gm3',
            'class': 'form-control',
            'multiple': True,
        }))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)]


class ElkeUploadForm(forms.Form):
    dateien = MultipleFileField(
        label='ELKE-Dateien auswählen (.ELK, .GM3)',
        help_text='Mehrere Dateien gleichzeitig möglich (Strg+Klick oder Shift+Klick)',
    )

    def clean_dateien(self):
        files = self.cleaned_data.get('dateien') or []
        allowed = {'.elk', '.gm3'}
        for f in files:
            ext = '.' + f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
            if ext not in allowed:
                raise forms.ValidationError(
                    f'Ungültiger Dateityp: {f.name}. Nur .ELK und .GM3 sind erlaubt.'
                )
        return files
