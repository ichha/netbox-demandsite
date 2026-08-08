from django.urls import path
from . import views

urlpatterns = [
    path('', views.DemandsiteListView.as_view(), name='demandsite_list'),
    path('server/', views.DemandsiteServerView.as_view(), name='demandsite_server'),
    path('sites/<str:siteid>/', views.DemandsiteDetailView.as_view(), name='demandsite_detail'),
]

