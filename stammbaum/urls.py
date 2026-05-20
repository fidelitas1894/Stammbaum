from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('stammbaum/', views.stammbaum, name='stammbaum'),
    path('stammbaum/gesamt/', views.stammbaum_gesamt, name='stammbaum_gesamt'),
    path('api/stammbaum/gesamt/', views.stammbaum_gesamt_json, name='stammbaum_gesamt_json'),
    path('api/stammbaum/<int:person_id>/', views.stammbaum_json, name='stammbaum_json'),
    path('personen/', views.personen_liste, name='personen_liste'),
    path('personen/neu/', views.person_neu, name='person_neu'),
    path('personen/<int:pk>/', views.person_detail, name='person_detail'),
    path('personen/<int:pk>/bearbeiten/', views.person_bearbeiten, name='person_bearbeiten'),
    path('karte/', views.karte, name='karte'),
    path('api/karte/', views.karte_json, name='karte_json'),
    path('api/geocode/<str:ort_name>/', views.geocode_ort, name='geocode_ort'),
    path('statistiken/', views.statistiken, name='statistiken'),
    path('suche/', views.suche, name='suche'),
    path('verwandtschaft/', views.verwandtschaft, name='verwandtschaft'),
    path('api/verwandtschaft/', views.verwandtschaft_api, name='verwandtschaft_api'),
    path('export/gedcom/', views.export_gedcom, name='export_gedcom'),
    path('export/csv/', views.export_csv, name='export_csv'),
    path('import/', views.import_view, name='import'),
]
