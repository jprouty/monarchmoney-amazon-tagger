from collections import defaultdict
from copy import deepcopy
import datetime
from dateutil.parser import parse as dateutil_parse
import logging
import re
from typing import Any, List, Optional

from monarchmoneyamazontagger import category
from monarchmoneyamazontagger.micro_usd import MicroUSD
from monarchmoneyamazontagger.my_progress import NoProgress

logger = logging.getLogger(__name__)


def truncate_title(title, target_length, base_str=None):
    words = []
    if base_str:
        words.extend([w for w in base_str.split(" ") if w])
        target_length -= len(base_str)
    for word in title.strip().split(" "):
        if len(word) / 2 < target_length:
            words.append(word)
            target_length -= len(word) + 1
        else:
            break
    truncated = " ".join(words)
    # Remove any trailing symbol-y crap.
    while truncated and truncated[-1] in ",.-([]{}\\/|~!@#$%^&*_+=`'\" ":
        truncated = truncated[:-1]
    return truncated


class Category(object):
    """A Monarch Money category."""

    id: str
    name: str
    icon: Optional[str]
    __typename: str = "Category"

    def __init__(self, id, name, icon=None):
        self.id = id
        self.name = name
        self.icon = icon

    # def update_category_id(self, categories):
    #     if self.name in categories:
    #         self.id = categories[self.name]["id"]

    def __repr__(self):
        return f"{self.name}({self.id})"


class Merchant:
    """A Monarch Money category."""

    id: str
    name: str
    logoUrl: Optional[str]
    recurringTransactionStream: Optional[Any]
    transactionsCount: Optional[int]
    __typename: str = "Merchant"

    def __init__(
        self,
        id,
        name,
        logoUrl=None,
        transactionsCount=None,
        recurringTransactionStream=None,
    ):
        self.id = id
        self.name = name
        self.logoUrl = logoUrl
        self.transactionsCount = transactionsCount
        self.recurringTransactionStream = recurringTransactionStream


class AccountSubtype:
    """A Monarch Money account subtype."""

    display: str
    __typename: str = "AccountSubtype"

    def __init__(self, display):
        self.display = display


class Account:
    """A Monarch Money account."""

    id: str
    displayName: str
    icon: Optional[str]
    logoUrl: Optional[str]  # Example: "data:image/png;base64,..."
    mask: Optional[str]  # Not present for get_transactions()
    subtype: Optional[AccountSubtype]
    __typename: str = "Account"

    def __init__(
        self, id, displayName, icon=None, logoUrl=None, mask=None, subtype=None
    ):
        self.id = id
        self.displayName = displayName
        self.icon = icon
        self.logoUrl = logoUrl
        self.mask = mask
        self.subtype = subtype


class SplitTransaction:
    """A Monarch Money split transaction."""

    id: str
    amount: float
    merchant: Merchant
    category: Category

    def __init__(self, id, amount, merchant, category):
        self.id = id
        self.amount = amount
        self.merchant = merchant
        self.category = category


def strptime_or_none(datetime_str, format_str):
    if not datetime_str or not format_str:
        return None
    try:
        return datetime.datetime.strptime(datetime_str, format_str)
    except ValueError:
        return None


class Transaction:
    """A Monarch Money transaction."""

    id: str
    amount: MicroUSD  # Add comment to signage and it's meaning as a debit/credit on both types of accounts (savings / cc / loan)
    date: datetime.date  # Parse as: "2024-01-03",
    originalDate: Optional[datetime.date]  # Parse as: "2024-01-03",
    pending: bool
    needsReview: bool
    needsReviewByUser: Optional[Any]  # Need example
    reviewStatus: Optional[Any]  # Need example
    reviewedAt: Optional[datetime.datetime]  # Need example
    reviewedByUser: Optional[Any]  # Need example
    isRecurring: bool
    isSplitTransaction: bool
    hideFromReports: bool
    # For split transactions only: parent that have one or more splits.
    splitTransactions: List["Transaction"]
    # For split transactions only: the transaction ID of the parent transaction.
    originalTransaction: Optional[str]
    createdAt: datetime.datetime  # "2024-01-03T15:43:18.634009+00:00",
    updatedAt: datetime.datetime  # "2024-01-03T16:32:08.539592+00:00",

    category: Category
    merchant: Merchant
    account: Account

    notes: Optional[str]
    tags: List[Any]  # Need example
    attachments: List[Any]  # Need example
    goal: Optional[Any]  # Need example
    plaidName: Optional[str]  # example: "MACYS AUTO PYMT 240102",
    __typename: str = "Transaction"

    matched = False
    # AmazonCharges:
    charges = []
    item = None  # Set in the case of itemized new transactions.

    def __init__(
        self,
        id,
        amount,
        date,
        originalDate,
        pending,
        needsReview,
        isRecurring,
        isSplitTransaction,
        hideFromReports,
        splitTransactions,
        originalTransaction,
        createdAt,
        updatedAt,
        category,
        merchant,
        account,
        notes,
        tags,
        attachments,
        goal,
        plaidName,
    ):
        self.id = id
        self.amount = MicroUSD.from_float(amount)
        # Required - will raise ValueError if not valid:
        self.date = datetime.datetime.strptime(date, "%Y-%m-%d").date()
        # Optional:
        originalDate = strptime_or_none(originalDate, "%Y-%m-%d")
        if originalDate:
            self.originalDate = originalDate.date()
        self.pending = pending
        self.needsReview = needsReview
        self.isRecurring = isRecurring
        self.isSplitTransaction = isSplitTransaction
        self.hideFromReports = hideFromReports
        self.splitTransactions = splitTransactions
        self.originalTransaction = originalTransaction
        self.createdAt = dateutil_parse(createdAt)
        self.updatedAt = dateutil_parse(updatedAt)

        self.category = Category(**category)
        self.merchant = Merchant(**merchant)
        self.account = Account(**account)

        self.notes = notes
        self.tags = tags
        self.attachments = attachments
        self.goal = goal
        self.plaidName = plaidName

    def clone(self):
        """Returns a clone of this Transaction."""
        clone = deepcopy(self)
        # Itemized should NOT have this info, otherwise there are some lovely cycles.
        clone.matched = False
        clone.charges = []
        return clone

    def match(self, charges):
        self.matched = True
        self.charges = charges

    # def bastardize(self):
    #     """Severs the child from the parent making this a parent itself."""
    #     self.parent_id = None

    # def update_category_id(self, categories):
    #     self.category.update_category_id(categories)

    def get_compare_tuple(self, ignore_category=False):
        """Returns a 3-tuple used to determine if 2 transactions are equal."""
        base = (self.merchant.name, str(self.amount), self.notes)
        return base if ignore_category else base + (self.category.name,)

    def dry_run_str(self, ignore_category=False):
        return (
            f'{self.date.strftime("%Y-%m-%d")} \t'
            f"{str(self.amount)} \t"
            f'{"--IGNORED--" if ignore_category else self.category} \t'
            f"{self.merchant.name}"
        )

    def __repr__(self):
        return (
            f"Transaction({self.id}): {str(self.amount)} "
            f"{self.date} {self.merchant.name} {self.category} "
            f'{"with notes" if self.notes else ""}'
        )

    @classmethod
    def parse_from_json(cls, json_objs: List[Any], progress=NoProgress()):
        result = []
        for json_obj in json_objs:
            result.append(cls(**json_obj))
            progress.next()
        return result

    @staticmethod
    def sum_amounts(trans):
        return sum([t.amount for t in trans])

    # @staticmethod
    # def unsplit(trans):
    #     """Reconstitutes splits/itemizations into parent transaction."""
    #     parent_id_to_trans = defaultdict(list)
    #     result = []
    #     for t in trans:
    #         if t.parent_id:
    #             parent_id_to_trans[t.parent_id].append(t)
    #         else:
    #             result.append(t)

    #     for parent_id, children in parent_id_to_trans.items():
    #         parent = deepcopy(children[0])

    #         parent.id = parent_id
    #         parent.bastardize()
    #         parent.amount = round_micro_usd_to_cent(Transaction.sum_amounts(children))
    #         parent.children = children

    #         result.append(parent)

    #     return result

    @staticmethod
    def old_and_new_are_identical(old, new, ignore_category=False):
        """Returns True if there is zero difference between old and new."""
        old_set = set(
            [c.get_compare_tuple(ignore_category) for c in old.children]
            if old.children
            else [old.get_compare_tuple(ignore_category)]
        )
        new_set = set([t.get_compare_tuple(ignore_category) for t in new])
        return old_set == new_set


def itemize_new_trans(new_trans, prefix):
    # Add a prefix to all itemized transactions for easy keyword searching
    # within Monarch Money. Use the same prefix, based on if the original transaction
    for nt in new_trans:
        nt.description = prefix + nt.description

    # Turns out the first entry is typically displayed last in the Monarch Money
    # UI. Reverse everything for ideal readability.
    return new_trans[::-1]


NON_ITEM_DESCRIPTIONS = set(
    ["Misc Charge (Gift wrap, etc)", "Promotion(s)", "Shipping", "Tax adjustment"]
)


def summarize_title(titles, prefix):
    trun_len = (100 - len(prefix) - 2 * len(titles)) / len(titles)
    return prefix + (", ".join([truncate_title(t, trun_len) for t in titles]))


def summarize_new_trans(t, new_trans, prefix):
    # When not itemizing, create a description by concatenating the items. Store
    # the full information in the transaction notes. Category is untouched when
    # there's more than one item (this is why itemizing is better!).
    title = summarize_title(
        [
            nt.description
            for nt in new_trans
            if nt.description not in NON_ITEM_DESCRIPTIONS
        ],
        prefix,
    )
    notes = "{}\nItem(s):\n{}".format(
        new_trans[0].notes, "\n".join([" - " + nt.description for nt in new_trans])
    )

    summary_trans = deepcopy(t)
    summary_trans.description = title
    if (
        len([nt for nt in new_trans if nt.description not in NON_ITEM_DESCRIPTIONS])
        == 1
    ):
        summary_trans.category = new_trans[0].category
    else:
        summary_trans.category.name = category.DEFAULT_CATEGORY
    summary_trans.notes = notes
    return [summary_trans]
