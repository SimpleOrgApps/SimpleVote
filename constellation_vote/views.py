import json

from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.core import serializers
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.views import View

from guardian.shortcuts import assign_perm

from constellation_base.models import GlobalTemplateSettings

from .models import (
    Ballot,
    BallotItem,
    Poll,
    PollOption
)

from py3votecore.stv import STV
from py3votecore.plurality_at_large import PluralityAtLarge

def index(request):
    """Return index text"""
    return HttpResponse("foo!")


def view_list(request):
    ''' Returns a page that includes a list of submitted forms '''
    template_settings = GlobalTemplateSettings(allowBackground=False)
    template_settings = template_settings.settings_dict()
    polls = Poll.objects.all()

    return render(request, 'constellation_vote/list.html', {
        'template_settings': template_settings,
        'polls': polls,
    })


class manage_poll(View):
    def get(self, request, poll_id=None):
        """ Returns a page that allows for the creation of a poll """
        template_settings = GlobalTemplateSettings(allowBackground=False)
        template_settings = template_settings.settings_dict()

        poll = None
        pollOptions = None

        # If poll_id was set, get that poll and its options to edit
        if poll_id is not None:
            poll = Poll.objects.get(pk=poll_id)
            pollOptions = serializers.serialize(
                "json", PollOption.objects.filter(poll=poll))

        return render(request, 'constellation_vote/manage-poll.html', {
            'template_settings': template_settings,
            'poll': poll,
            'pollOptions': pollOptions,
            'visible_groups': [(g.name, g.pk) for g in Group.objects.all()]
            })

    def post(self, request, poll_id=None):
        """ Creates a poll """
        pollDict = json.loads(request.POST["data"])
        try:
            # Try creating the poll and if that fails, then we won't put in
            # options
            pollInfoDict = pollDict["meta"]
            pollOptionsDict = pollDict["options"]
            poll, c = Poll.objects.get_or_create(pk=poll_id)

            poll.title = pollInfoDict["title"]
            poll.desc = pollInfoDict["desc"]

            if pollOptionsDict["starts"] != "":
                poll.starts = datetime.strptime(pollOptionsDict["starts"],
                                                "%m/%d/%Y %H:%M")
            if pollOptionsDict["ends"] != "":
                poll.ends = datetime.strptime(pollOptionsDict["ends"],
                                              "%m/%d/%Y %H:%M")

            owning_group = Group.objects.get(name=pollOptionsDict["owner"])
            poll.owned_by = owning_group

            # Checkboxes don't POST if they aren't checked
            if "results_visible" in pollOptionsDict:
                poll.results_visible = True
            else:
                poll.results_visible = False

            poll.full_clean()
            poll.save()

            # Now we create the options
            for optionDict in pollDict["choices"]:
                opt_ID = None
                if "pk" in optionDict and optionDict["pk"]:
                    opt_ID = optionDict["pk"]
                opt, c = PollOption.objects.get_or_create(pk=opt_ID)
                opt.poll = poll
                opt.text = optionDict["text"]
                if "desc" in optionDict:
                    opt.desc = optionDict["desc"]
                if "active" in optionDict:
                    opt.active = optionDict["active"]
                opt.save()
            # If we've made it this far, the poll itself is saved
            # Now we can set the permissions on this object
            visibleGroup = Group.objects.get(name=pollOptionsDict["visible"])
            assign_perm("poll_visible", visibleGroup, poll)

        except Group.DoesNotExist:
            if poll_id is None:
                poll.delete()
            return HttpResponseBadRequest("Permission groups must be selected")
        except ValidationError:
            if poll_id is None:
                poll.delete()
            return HttpResponseBadRequest("Poll could not be created!")

        return HttpResponse(pollDict)


class ballot_view(View):
    def get(self, request, poll_id):
        """Return a ballot for casting or editing"""
        template_settings = GlobalTemplateSettings(allowBackground=False)
        template_settings = template_settings.settings_dict()

        poll = Poll.objects.get(pk=poll_id)
        ballot = None
        selected_options = []

        # If user has already filled out the poll once.
        # Return their previous ballot
        if Ballot.objects.filter(poll=poll, owned_by=request.user).exists():

            ballot = Ballot.objects.get(poll=poll, owned_by=request.user)

            # Maintain order
            selected_option_pks = BallotItem.objects \
                .select_related('poll_option') \
                .filter(ballot=ballot) \
                .values_list('poll_option', flat=True)

            for pk in selected_option_pks:
                selected_options.append(PollOption.objects.get(pk=pk))

            # Everything else
            poll_options = PollOption.objects.filter(poll=poll).exclude(
                pk__in=selected_option_pks)
        else:
            poll_options = PollOption.objects.filter(poll=poll)

        return render(request, 'constellation_vote/ballot.html', {
            'template_settings': template_settings,
            'poll': poll,
            'poll_options': poll_options,
            'selected_options': selected_options,
            })

    def post(self, request, poll_id):
        '''Vote or Edit a request'''
        poll = Poll.objects.get(pk=poll_id)
        try:
            with transaction.atomic():
                ballot, _ = Ballot.objects.get_or_create(poll=poll,
                                                         owned_by=request.user)
                ballot.full_clean()
                ballot.save()
                ballot.selected_options.clear()
                for i, option in enumerate(json.loads(request.POST['data'])):
                    pollOption = PollOption.objects.get(pk=option)
                    item = BallotItem(ballot=ballot,
                                      poll_option=pollOption,
                                      order=i)
                    item.full_clean()
                    item.save()
        except:
            return HttpResponseBadRequest("Vote could not be cast.")

        return HttpResponse()


def view_poll_results(request, poll_id):
    """Display poll results, summing up the election on load"""
    template_settings = GlobalTemplateSettings(allowBackground=False)
    template_settings = template_settings.settings_dict()
    poll = Poll.objects.get(pk=poll_id)
    b = Ballot.objects.filter(poll=poll)
    ballots = []
    for o in b.iterator():
        ballots.append(o.to_ballot())

    #results = PluralityAtLarge(ballots, required_winners=3).as_dict()
    results = STV(ballots, required_winners=2).as_dict()

    options = PollOption.objects.filter(poll=poll)

    return render(request, "constellation_vote/view_results.html", {
        'template_settings': template_settings,
        'results': results,
        'options': options,
    })

# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------


@login_required
def view_dashboard(request):
    '''Return a card that will appear on the main dashboard'''

    return render(request, 'constellation_vote/dashboard.html')
