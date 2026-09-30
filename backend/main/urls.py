from django.urls import path

from .views import *
from content.sitemap import sitemap_xml, robots_txt
from content.views import preview_page

urlpatterns = [
    path("", index, name="index"),
    path('about/', about, name='about'),
    path('areas-we-serve/', areas_we_serve, name='areas_we_serve'),
    path('contact/', contact, name='contact'),
    path('sitemap.xml', sitemap_xml, name='sitemap_xml'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('preview/<slug:slug>/', preview_page, name='content_preview'),

    # Services Group
    path('bathroom-remodeling/', bathroom_remodeling, name='bathroom'),
    path('kitchen-remodeling/', kitchen_remodeling, name='kitchen'),
    path('basement-finishing/', basement_finishing, name='basement'),
    path('home-additions/', home_additions, name='additions'),
    path('painting/', painting, name='painting'),
    path('home-improvement/', home_improvement, name='home_improvement'),
    path('patios-decks/', patios_decks, name='patios_decks'),
    path('cabinets/', cabinets, name='cabinets'),
    path('woodworking/', woodworking, name='woodworking'),
    path('hardscaping/', hardscaping, name='hardscaping'),
    path('walkway-designs/', walkway_designs, name='walkway_designs'),
    path('pergolas/', pergolas, name='pergolas'),
    path('lead-removal/', lead_removal, name='lead_removal'),
    path('shed-builder/', shed_builder, name='shed_builder'),
    path('lead-renovator/', lead_renovator, name='lead_renovator'),

    # Anything else that looks like a service slug is served from the CRM.
    # Last in the list on purpose: the routes above are the captured pages and
    # must keep winning, byte for byte, while a service added afterwards still
    # has a real address instead of a 404 from its own catalogue row.
    path('<slug:slug>/', service_page, name='service_page'),
]

