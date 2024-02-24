from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, View
from .models import Transaction
from .constants import DEPOSIT, WITHDRAWAL,LOAN, LOAN_PAID, TRANSFER, RECEIVE_MONEY
from transactions.forms import (
    DepositForm,
    WithdrawForm,
    LoanRequestForm,
    TransferMoneyForm
)
from datetime import datetime
from django.db.models import Sum
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template.loader import render_to_string
# Create your views here.

def send_transaction_email(user, amount, subject, template):
    meassage = render_to_string(template,{
        'user': user,
        'amount': amount
    })
    send_email = EmailMultiAlternatives(subject,'', to=[user.email])
    send_email.attach_alternative(meassage,'text/html')
    send_email.send()

class TransactionCreateMixin(LoginRequiredMixin, CreateView):
    template_name = 'transactions/transaction_form.html'
    model = Transaction
    title = ''
    success_url = reverse_lazy('transaction_report')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({'account': self.request.user.account})
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({'title': self.title})
        return context

class DepositMoneyView(TransactionCreateMixin):
    form_class = DepositForm
    title = 'Deposit Form'

    def get_initial(self):
        initial = {'transaction_type': DEPOSIT}
        return initial
    
    def form_valid(self, form):
        amount = form.cleaned_data.get('amount')
        account = self.request.user.account
        account.balance +=amount
        account.save(update_fields=['balance'])
        messages.success(self.request, f'{"{:,.2f}".format(float(amount))}$ was deposited to your account successfully')
        send_transaction_email(self.request.user, amount, 'Deposit Message', 'transactions/deposit_email.html')
        return super().form_valid(form)

class WithdrawMoneyView(TransactionCreateMixin):
    form_class = WithdrawForm
    title = 'Withdraw Form'

    def get_initial(self):
        initial = {'transaction_type': WITHDRAWAL}
        return initial
    
    def form_valid(self, form):
        amount = form.cleaned_data.get('amount')
        account = self.request.user.account
        account.balance -= amount
        account.save(update_fields=['balance'])
        messages.success(self.request, f'Successfully withdrawn {"{:,.2f}".format(float(amount))}$ from your account')
        send_transaction_email(self.request.user,amount,'Withdrawl Message', 'transactions/withdraw_email.html')
        return super().form_valid(form)

class LoanRequestView(TransactionCreateMixin):
    form_class = LoanRequestForm
    title = 'Request For Loan'

    def get_initial(self):
        initial = {'transaction_type': LOAN}
        return initial

    def form_valid(self, form):
        amount = form.cleaned_data.get('amount')
        current_loan_count = Transaction.objects.filter(
            account=self.request.user.account,transaction_type=3,loan_approve=True).count()
        if current_loan_count >= 3:
            return HttpResponse("You have cross the loan limits")
        messages.success(
            self.request,
            f'Loan request for {"{:,.2f}".format(float(amount))}$ submitted successfully'
        )
        send_transaction_email(self.request.user,amount,'Loan Request','transactions/loan_email.html')
        return super().form_valid(form)

class TransactionReportView(LoginRequiredMixin, ListView):
    template_name = 'transactions/transaction_report.html'
    model = Transaction
    balance = 0
    
    def get_queryset(self):
        queryset = super().get_queryset().filter(account = self.request.user.account)
        start_date_str = self.request.GET.get('start_date')
        end_date_str = self.request.GET.get('end_date')

        if start_date_str and end_date_str:
            start_date = datetime.strptime(start_date_str,'%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str,'%Y-%m-%d').dend

            queryset = queryset.filter(timestamp__date__gte= start_date, timestamp__date__lte = end_date)
            self.balance = Transaction.objects.filter(
                timestamp__date__gte= start_date, timestamp__date__lte = end_date
            ).aggregate(Sum('amount'))['amount__sum']
        else:
            self.balance = self.request.user.account.balance

        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({'account': self.request.user.account})
        return context
    
class PayLoanView(LoginRequiredMixin, View):
    def get(self, request, loan_id):
        loan = get_object_or_404(Transaction, id=loan_id)
        if loan.loan_approve:
            user_account = loan.account

            if loan.amount < user_account.balance:
                user_account.balance -= loan.amount
                loan.balance_after_transaction = user_account.balance
                user_account.save()
                loan.loan_approve = True
                loan.transaction_type = LOAN_PAID
                loan.save()
                return redirect('loan_list')
            else:
                messages.error(self.request, f'Loan amount is {loan.amount} greater than available balance')
        return redirect('loan_list')

class LoanListView(LoginRequiredMixin,ListView):
    model = Transaction
    template_name = 'transactions/loan_request.html'
    context_object_name = 'loans'
    def get_queryset(self):
        user_account = self.request.user.account
        queryset = Transaction.objects.filter(account=user_account,transaction_type=3)
        return queryset

class TransferMoneyView(View):
    form_class = TransferMoneyForm
    def get_initial(self):
        return {'transaction_type': TRANSFER}

    def get(self, request):
        form = self.form_class()
        return render(request, 'transactions/transfer_balance.html', {'form': form,'title': 'Transfer Balance'})

    def post(self, request):
        form = self.form_class(data=request.POST)
        if form.is_valid():
            recipient_username = form.cleaned_data['recipient_username']
            amount = form.cleaned_data['amount']
            try:
                recipient_user = User.objects.get(username=recipient_username)
                recipient_account = recipient_user.account
            except User.DoesNotExist:
                messages.error(request, f"Recipient '{recipient_username}' does not exist.")
                return redirect('transfer')
            sender_account = request.user.account
            if sender_account.balance < amount:
                messages.error(request, 'Insufficient balance.')
                return redirect('transfer')

            sender_account.balance -= amount
            sender_account.save()
            send_transaction_email(request.user,amount,'Send Money', 'transactions/send_money.html')
            recipient_account.balance += amount
            recipient_account.save()
            send_transaction_email(recipient_user,amount,'Recived Money', 'transactions/receive_money.html')
            sender_transaction = Transaction.objects.create(
                account=sender_account,
                transaction_type=TRANSFER,
                amount=amount,
                timestamp=timezone.now(),
                balance_after_transaction=sender_account.balance
            )
            recipient_transaction = Transaction.objects.create(
                account=recipient_account,
                transaction_type=RECEIVE_MONEY,
                amount=amount,
                timestamp=timezone.now(),
                balance_after_transaction=recipient_account.balance
            )
            return redirect('transfer')