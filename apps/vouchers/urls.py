from django.urls import path
from . import views

app_name = 'vouchers'

urlpatterns = [
    path('redeem/', views.redeem_voucher, name='redeem'),
]