from django.urls import path
from . import views

urlpatterns = [
    path('', views.DemandsiteListView.as_view(), name='demandsite_list'),
    path('sites/<str:siteid>/', views.DemandsiteDetailView.as_view(), name='demandsite_detail'),
]
