from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('login/', views.admin_login, name='admin_login'),
    path('revenue/login/', views.shareholder_login, name='shareholder_login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('revenue/', views.revenue_dashboard, name='revenue_dashboard'),
    path('revenue/total-capital/', views.update_total_capital, name='update_total_capital'),
    path('revenue/subscription-cost/', views.update_subscription_cost, name='update_subscription_cost'),
    path('revenue/withdrawals/request/', views.request_withdrawal, name='request_withdrawal'),
    path('revenue/withdrawals/<int:request_id>/decide/', views.decide_withdrawal, name='decide_withdrawal'),
    path('revenue/shares/request/', views.request_share_increase, name='request_share_increase'),
    path('revenue/shares/<int:request_id>/decide/', views.decide_share_increase, name='decide_share_increase'),
    path('account/', views.my_account, name='my_account'),
    path('shareholders/activity/', views.shareholder_activity, name='shareholder_activity'),
    path('payments/', views.payment_management, name='payments'),
    path('subscriptions/', views.subscription_management, name='subscriptions'),
    path('subscriptions/<int:subscription_id>/add-time/', views.add_subscription_time, name='add_subscription_time'),
    path('subscriptions/time-adjustments/', views.time_adjustments_log, name='time_adjustments_log'),
    path('vouchers/', views.voucher_management, name='vouchers'),
    path('vouchers/<int:batch_id>/approve/', views.approve_voucher_batch, name='approve_voucher_batch'),
    path('feedback/submit/', views.submit_feedback, name='submit_feedback'),
    path('feedback/', views.feedback_list, name='feedback_list'),
    path('feedback/<int:feedback_id>/toggle-contacted/', views.toggle_feedback_contacted, name='toggle_feedback_contacted'),
]
