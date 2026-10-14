"""Email notifications for leave requests.

Emails are sent only after the surrounding database transaction commits, so a
failed or rolled-back operation never sends one. A delivery failure is logged
and never breaks the leave operation itself.
"""

import logging

from django.conf import settings
from django.core.mail import send_mass_mail
from django.db import transaction
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _format_date(day, with_year=True):
    return f"{day.day} {day:%b %Y}" if with_year else f"{day.day} {day:%b}"


def _format_dates(leave_request):
    """'9 Nov 2026', '9 Nov 2026 (half day)', '9 Nov – 13 Nov 2026'."""
    start, end = leave_request.start_date, leave_request.end_date
    if start == end:
        return f"{_format_date(start)}{' (half day)' if leave_request.half_day else ''}"
    return f"{_format_date(start, with_year=start.year != end.year)} – {_format_date(end)}"


def _format_days(days):
    value = float(days)
    text = str(int(value)) if value.is_integer() else f"{value:.1f}"
    return f"{text} day" if value == 1 else f"{text} days"


def _context(leave_request):
    return {
        "employee_name": leave_request.employee.get_full_name() or leave_request.employee.email,
        "leave_type": leave_request.leave_type.name,
        "dates": _format_dates(leave_request),
        "days": _format_days(leave_request.days),
        "reason": leave_request.reason,
        "status": leave_request.get_status_display().lower(),
        "reviewer_name": (
            (leave_request.reviewed_by.get_full_name() or leave_request.reviewed_by.email)
            if leave_request.reviewed_by
            else ""
        ),
        "review_comment": leave_request.review_comment,
        "url": f"{settings.FRONTEND_URL}/requests/{leave_request.pk}",
    }


def _queue(template, subject, recipients, leave_request, exclude=None):
    """Render now and send one email per recipient after the transaction commits."""
    emails = sorted(
        {
            user.email
            for user in recipients
            if user is not None and user.is_active and user.email and user != exclude
        }
    )
    if not emails:
        return
    body = render_to_string(f"leaves/email/{template}.txt", _context(leave_request))
    messages = [(subject, body, None, [email]) for email in emails]

    def deliver():
        try:
            send_mass_mail(messages)
        except Exception:
            logger.exception("Could not send %s email for leave request %s", template, leave_request.pk)

    transaction.on_commit(deliver)


def leave_submitted(leave_request, approvers):
    context_name = leave_request.employee.get_full_name() or leave_request.employee.email
    subject = f"Leave request from {context_name}: {_format_dates(leave_request)}"
    _queue("submitted", subject, approvers, leave_request, exclude=leave_request.employee)


def leave_reviewed(leave_request):
    """Approved or rejected: tell the employee."""
    subject = f"Your leave request was {leave_request.get_status_display().lower()}: {_format_dates(leave_request)}"
    template = "approved" if leave_request.status == leave_request.Status.APPROVED else "rejected"
    _queue(template, subject, [leave_request.employee], leave_request, exclude=leave_request.reviewed_by)


def leave_cancelled(leave_request, recipients):
    """A pending request was withdrawn, or approved leave was cancelled."""
    context_name = leave_request.employee.get_full_name() or leave_request.employee.email
    subject = f"Leave request cancelled by {context_name}: {_format_dates(leave_request)}"
    _queue("cancelled", subject, recipients, leave_request, exclude=leave_request.employee)
