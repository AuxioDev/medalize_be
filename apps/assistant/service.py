"""AI symptom assistant pipeline (Anthropic Claude).

Two-stage flow per user message:

1. Gate — a small/fast model classifies the latest user message (with
   conversation context) as medical or not. Non-medical questions get a canned
   localized refusal without ever calling the main model.
2. Chat — the main model answers with a safety-focused system prompt, patient
   context from ``PatientProfile``, and a ``search_doctors`` tool backed by a
   real Django ORM query. A localized disclaimer is always appended.

``classify_is_medical`` and ``call_assistant`` are thin wrappers around the
Anthropic API so tests can mock them without touching the network.
"""
import datetime
import json

from django.conf import settings
from django.db.models import Avg, Count, F
from django.utils import timezone

from apps.notifications.i18n import recipient_language
from apps.users.i18n import specialization_label

from .i18n import assistant_message
from .models import Message

_GATE_MODEL = 'claude-haiku-4-5-20251001'
_CHAT_MODEL = 'claude-sonnet-5'
_RECENT_MESSAGES_LIMIT = 10
_MAX_TOOL_ITERATIONS = 3

_SPECIALIZATION_CODES = [
    'general_practice', 'cardiology', 'dermatology', 'neurology', 'orthopedics',
    'pediatrics', 'psychiatry', 'gynecology', 'urology', 'ophthalmology',
    'ent', 'oncology', 'endocrinology', 'gastroenterology', 'pulmonology',
    'radiology', 'anesthesiology', 'emergency_medicine',
]

_GATE_SYSTEM_PROMPT = (
    'You are a strict classifier for a medical symptom assistant. '
    'Given a conversation, decide whether the LAST user message is related to '
    'health, medicine, symptoms, conditions, medications, or finding a doctor '
    '(follow-ups to an ongoing medical discussion count as medical). '
    'Answer with exactly one word: MEDICAL or NOT_MEDICAL.'
)

_SEARCH_DOCTORS_TOOL = {
    'name': 'search_doctors',
    'description': (
        'Search the Medalize database for verified doctors of a given '
        'specialization, optionally filtered by city. Returns up to 5 doctors '
        'sorted by patient rating. Use this when you recommend that the '
        'patient sees a specialist.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'specialization': {
                'type': 'string',
                'enum': _SPECIALIZATION_CODES,
                'description': 'Specialization code to search for.',
            },
            'city': {
                'type': 'string',
                'description': 'Optional city name to filter doctors by.',
            },
        },
        'required': ['specialization'],
    },
}

# Forced tool-call (see generate_summary()) that turns the free-form chat
# transcript into a guaranteed-structured, patient-shareable report instead of
# parsing free text.
_SUMMARY_TOOL = {
    'name': 'record_summary',
    'description': 'Record a structured, patient-shareable summary of this conversation.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'summary_text': {
                'type': 'string',
                'description': 'A short, patient-friendly paragraph summarizing the discussion so far.',
            },
            'possible_conditions': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string'},
                        'likelihood': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                        'note': {'type': 'string'},
                    },
                    'required': ['name', 'likelihood'],
                },
            },
            'urgency': {
                'type': 'string',
                'enum': ['routine', 'soon', 'urgent', 'emergency'],
                'description': (
                    'routine = no rush; soon = see a doctor within days; '
                    'urgent = within 24h; emergency = seek emergency care now.'
                ),
            },
            'recommended_specialization': {
                'type': 'string',
                'enum': _SPECIALIZATION_CODES,
            },
        },
        'required': ['summary_text', 'possible_conditions', 'urgency', 'recommended_specialization'],
    },
}

_SUMMARY_SYSTEM_PROMPT_LINES = [
    'You are the Medalize AI symptom assistant. Analyze the ENTIRE conversation '
    'above between the patient and the assistant, then call the record_summary '
    'tool exactly once with a structured, patient-shareable summary of it.',
    'Strict rules:',
    '- NEVER give a diagnosis. possible_conditions are possible, unconfirmed '
    'directions worth discussing with a real doctor — not a list of diagnoses.',
    '- NEVER name specific medication dosages.',
    '- Set urgency honestly based on the symptoms actually discussed: '
    'routine = no rush; soon = see a doctor within days; urgent = within 24h; '
    'emergency = seek emergency care now.',
    '- summary_text must be a short, patient-friendly paragraph, not clinical jargon.',
]


def _build_summary_system_prompt(lang):
    parts = list(_SUMMARY_SYSTEM_PROMPT_LINES)
    parts.append(f"- Respond in the patient's language (language code: {lang}).")
    return '\n'.join(parts)


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def classify_is_medical(recent_messages):
    """recent_messages: list of {'role': 'user'|'assistant', 'content': str}.
    Returns True/False. Kept as its own function so tests can mock it."""
    response = _client().messages.create(
        model=_GATE_MODEL,
        max_tokens=10,
        system=_GATE_SYSTEM_PROMPT,
        messages=recent_messages,
    )
    text = ''.join(
        block.text for block in response.content if block.type == 'text'
    ).strip().upper()
    # First-word check rather than strict equality — models occasionally add
    # punctuation. Anything unrecognised falls through to the medical path so
    # a flaky gate degrades to a normal (safe) assistant answer.
    return not text.startswith('NOT')


def call_assistant(system_prompt, messages, tools, tool_choice=None):
    """Returns the raw Anthropic API response object (may contain tool_use
    blocks). Its own function so tests can mock it without hitting the network.

    ``tool_choice`` is optional and, when omitted, is not forwarded to
    ``messages.create`` at all — existing call sites (handle_user_message)
    keep relying on the model's default (auto) tool choice unchanged. Passing
    e.g. {'type': 'tool', 'name': 'record_summary'} forces a specific tool
    call, guaranteeing a structured response instead of free text."""
    kwargs = dict(
        model=_CHAT_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=messages,
        tools=tools,
    )
    if tool_choice is not None:
        kwargs['tool_choice'] = tool_choice
    return _client().messages.create(**kwargs)


def _age_from_dob(date_of_birth):
    today = datetime.date.today()
    return today.year - date_of_birth.year - (
        (today.month, today.day) < (date_of_birth.month, date_of_birth.day)
    )


def _patient_context_lines(patient):
    lines = []
    profile = getattr(patient, 'patient_profile', None)
    if profile is None:
        return lines
    if profile.date_of_birth:
        lines.append(f'Age: {_age_from_dob(profile.date_of_birth)}')
    if profile.allergies.strip():
        lines.append(f'Allergies: {profile.allergies.strip()}')
    if profile.chronic_conditions.strip():
        lines.append(f'Chronic conditions: {profile.chronic_conditions.strip()}')
    if profile.medications.strip():
        lines.append(f'Current medications: {profile.medications.strip()}')
    return lines


def _build_system_prompt(patient, lang):
    parts = [
        'You are the Medalize AI symptom assistant, helping patients understand '
        'their symptoms and find the right doctor.',
        'Strict rules:',
        '- NEVER give a diagnosis. You may discuss possible causes in general, '
        'educational terms only.',
        '- NEVER name specific medication dosages. Do not prescribe.',
        '- Always recommend an in-person doctor visit for significant, '
        'persistent, or worrying symptoms; for emergency symptoms (chest pain, '
        'difficulty breathing, stroke signs, severe bleeding) tell the patient '
        'to seek emergency care immediately.',
        '- When symptoms point to a specific area, name the relevant medical '
        'specialization and, where appropriate, call the search_doctors tool '
        'to suggest real doctors from the Medalize database. Valid '
        'specialization codes: ' + ', '.join(_SPECIALIZATION_CODES) + '.',
        '- Stay strictly on health and medical topics, even if the user '
        'insists otherwise.',
        f"- Respond in the patient's language (language code: {lang}).",
        '- Keep answers concise and easy to understand for a non-medical reader.',
    ]
    context_lines = _patient_context_lines(patient)
    if context_lines:
        parts.append('Patient context (from their profile):')
        parts.extend(f'- {line}' for line in context_lines)
    return '\n'.join(parts)


def search_doctors(specialization, city=None, lang='en'):
    """Top-5 verified doctors for a specialization (optionally in a city),
    ordered by average review rating. Deliberately a narrow standalone query —
    do not reuse/refactor DoctorListView for this."""
    from apps.users.models import User

    qs = (
        User.objects
        .filter(
            role=User.ROLE_DOCTOR,
            doctor_profile__is_verified=True,
            doctor_profile__specialization=specialization,
        )
        .select_related('doctor_profile')
        .prefetch_related('workplaces')
        .annotate(
            avg_rating=Avg('doctor_reviews__rating'),
            total_reviews=Count('doctor_reviews', distinct=True),
        )
    )
    if city:
        qs = qs.filter(workplaces__city__icontains=city).distinct()
    qs = qs.order_by(F('avg_rating').desc(nulls_last=True), 'first_name', 'last_name')[:5]

    results = []
    for doctor in qs:
        workplaces = list(doctor.workplaces.all())
        primary = next((w for w in workplaces if w.is_primary), None)
        if primary is None and workplaces:
            primary = workplaces[0]
        results.append({
            'id': str(doctor.id),
            'first_name': doctor.first_name,
            'last_name': doctor.last_name,
            'specialization': specialization,
            'specialization_display': specialization_label(specialization, lang),
            'average_rating': (
                round(doctor.avg_rating, 1) if doctor.avg_rating is not None else None
            ),
            'total_reviews': doctor.total_reviews,
            'city': primary.city if primary else '',
        })
    return results


def _touch_conversation(conversation):
    # auto_now only fires on Conversation.save(); message saves don't cascade.
    conversation.save(update_fields=['updated_at'])


def handle_user_message(conversation, patient, text):
    """Runs the full gate → chat → tools pipeline for one user message and
    returns the persisted assistant ``Message``."""
    lang = recipient_language(patient)

    Message.objects.create(
        conversation=conversation, role=Message.ROLE_USER, content=text
    )
    recent = list(
        conversation.messages.order_by('-created_at')[:_RECENT_MESSAGES_LIMIT]
    )[::-1]
    api_messages = [{'role': m.role, 'content': m.content} for m in recent]

    if not classify_is_medical(api_messages):
        reply = Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ASSISTANT,
            content=assistant_message('not_medical_refusal', lang),
        )
        _touch_conversation(conversation)
        return reply

    system_prompt = _build_system_prompt(patient, lang)
    tools = [_SEARCH_DOCTORS_TOOL]
    suggested_doctors = []

    response = call_assistant(system_prompt, api_messages, tools)
    for _ in range(_MAX_TOOL_ITERATIONS):
        tool_uses = [b for b in response.content if b.type == 'tool_use']
        if not tool_uses:
            break
        api_messages.append({'role': 'assistant', 'content': response.content})
        tool_results = []
        for tool_use in tool_uses:
            doctors = []
            if tool_use.name == 'search_doctors':
                doctors = search_doctors(
                    tool_use.input.get('specialization', ''),
                    city=tool_use.input.get('city') or None,
                    lang=lang,
                )
                existing_ids = {d['id'] for d in suggested_doctors}
                suggested_doctors.extend(
                    d for d in doctors if d['id'] not in existing_ids
                )
            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': tool_use.id,
                'content': json.dumps(doctors),
            })
        api_messages.append({'role': 'user', 'content': tool_results})
        response = call_assistant(system_prompt, api_messages, tools)

    final_text = ''.join(
        block.text for block in response.content if block.type == 'text'
    ).strip()
    disclaimer = assistant_message('disclaimer', lang)
    final_text = f'{final_text}\n\n{disclaimer}' if final_text else disclaimer

    reply = Message.objects.create(
        conversation=conversation,
        role=Message.ROLE_ASSISTANT,
        content=final_text,
        suggested_doctors=suggested_doctors,
    )
    _touch_conversation(conversation)
    return reply


def generate_summary(conversation, patient):
    """Analyzes the FULL conversation history (unlike handle_user_message,
    not capped to _RECENT_MESSAGES_LIMIT — a summary needs the whole thread)
    and forces a single record_summary tool call to get a guaranteed
    structured report. Persists it on the conversation and returns it."""
    lang = recipient_language(patient)
    all_messages = list(conversation.messages.order_by('created_at'))
    api_messages = [{'role': m.role, 'content': m.content} for m in all_messages]

    system_prompt = _build_summary_system_prompt(lang)
    response = call_assistant(
        system_prompt, api_messages, [_SUMMARY_TOOL],
        tool_choice={'type': 'tool', 'name': 'record_summary'},
    )
    tool_use = next(block for block in response.content if block.type == 'tool_use')

    conversation.summary = tool_use.input
    conversation.summary_generated_at = timezone.now()
    conversation.save(update_fields=['summary', 'summary_generated_at'])
    return conversation
