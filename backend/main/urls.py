from django.urls import path
from .views import *

urlpatterns = [
    path("", index, name="index"),
    path('about/', about, name='about'),
    path('areas-we-serve/', areas_we_serve, name='areas_we_serve'),
    path('contact/', contact, name='contact'),
    
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
]
