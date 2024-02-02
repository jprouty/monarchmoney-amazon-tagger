from copy import deepcopy
import csv
from datetime import datetime, timezone
from typing import List, Optional
from dateutil import parser
import io
import logging
from pprint import pformat
import re
import string

from monarchmoneyamazontagger import category
from monarchmoneyamazontagger.micro_usd import MicroUSD, CENT_MICRO_USD, MICRO_USD_EPS
from monarchmoneyamazontagger.mm import truncate_title
from monarchmoneyamazontagger.my_progress import no_progress_factory

logger = logging.getLogger(__name__)

PRINTABLE = set(string.printable)


ORDER_HISTORY_CSV_PATTERN = re.compile(
    r"Retail.OrderHistory.\d+/Retail.OrderHistory.\d+.csv"
)


def is_order_history_csv(zip_file_name):
    return bool(ORDER_HISTORY_CSV_PATTERN.match(zip_file_name))


def rm_leading_qty(item_title):
    """Removes the '2x Item Name' from the front of an item title."""
    return re.sub(r"^\d+x ", "", item_title)


def get_title(amzn_obj, target_length):
    # Also works for a Refund record.
    qty = amzn_obj.quantity
    base_str = None
    if qty > 1:
        base_str = str(qty) + "x"
    # Remove non-ASCII characters from the title.
    clean_title = "".join(filter(lambda x: x in PRINTABLE, amzn_obj.product_name))
    return truncate_title(clean_title, target_length, base_str)


CURRENCY_FIELD_NAMES = set(
    [
        "Unit Price",
        "Unit Price Tax",
        "Shipping Charge",
        "Total Discounts",
        "Total Owed",
        "Shipment Item Subtotal",
        "Shipment Item Subtotal Tax",
    ]
)

DATE_FIELD_NAMES = set(
    [
        "Order Date",
        "Ship Date",
    ]
)

# TODO: Fix quoting issue with Website".
RENAME_FIELD_NAMES = {
    "Carrier Name & Tracking Number": "tracking",
    'Website"': "website",
}

MULTI_SPLIT_BY_AND = set(
    ["Order Date", "Ship Date", "tracking", "Payment Instrument Type"]
)


def parse_from_csv_common(
    cls, csv_file, progress_label="Parse from CSV", progress_factory=no_progress_factory
):
    # contents = csv_file.read().decode()
    contents = csv_file.read().decode("utf-8")
    # Strip a leading FEFF if present.
    if contents[0:1] == "\ufeff":
        contents = contents[2:]

    num_records = sum(1 for c in contents if c == "\n") - 1
    result = []
    if not num_records:
        return result

    progress = progress_factory(progress_label, num_records)
    reader = csv.DictReader(io.StringIO(contents))
    # Convert input fieldnames into pythonic names. Feels somewhat naughty but works:
    reader.fieldnames = [
        fn.replace('"', "").replace(" ", "_").replace("&", "and").lower()
        for fn in reader.fieldnames
    ]
    for csv_dict in reader:
        result.append(cls(**csv_dict))
        progress.next()
    progress.finish()
    return result


def parse_amazon_date(date_str: str) -> datetime | None:
    if not date_str or date_str == "Not Available":
        return None
    return parser.parse(date_str)


def get_invoice_url(order_id: str) -> str:
    return (
        "https://www.amazon.com/gp/css/summary/print.html?ie=UTF8&"
        f"orderID={order_id}"
    )


def datetime_list_to_dates_str(dates: List[datetime]) -> str:
    return ", ".join([d.strftime("%Y-%m-%d") for d in dates])


class Charge:
    """A Charge represents a set of items corresponding to one payment.

    A Charge can have (TODO: validate each):
    - One or more items with one or more per quantity each
    - One or more tracking numbers
    - One or more shipment dates/times
    - One or more payment instruments, including partial or complete usage of gift cards.

    A Charge cannot have:
    - Items from different order IDs
    """

    matched = False
    trans_id = None
    items = []

    def __init__(self, items):
        self.items = items

    # def subtotal(self):
    #     return sum([i.amount_charged for i in self.items])

    # @staticmethod
    # def sum_subtotals(charges):
    #     return sum([o.subtotal for o in charges])

    def has_hidden_shipping_fee(self):
        # Colorado - https://tax.colorado.gov/retail-delivery-fee
        # "Effective July 1, 2022, Colorado imposes a retail delivery fee on
        # all deliveries by motor vehicle to a location in Colorado with at
        # least one item of tangible personal property subject to state sales
        # or use tax."
        # Rate July 2022 to June 2023: $0.27
        # This is not the case as of 8/31/2022 for Amazon Order Reports.
        # "Retailers that make retail deliveries must show the total of the
        # fees on the receipt or invoice as one item called “retail delivery
        # fees”."
        # TODO: Improve the ' CO ' Matching, consider a regex w/ a zip code element.
        ship_dates = self.ship_dates()
        return (
            " CO " in self.ship_address()
            and self.tax() > 0
            and ship_dates
            and max(ship_dates) >= datetime(2022, 7, 1, tzinfo=timezone.utc)
        )

    def hidden_shipping_fee(self) -> MicroUSD:
        return MicroUSD.from_float(0.27)

    def hidden_shipping_fee_note(self) -> str:
        return "CO Retail Delivery Fee"

    def total_by_items(self):
        return (
            Item.sum_totals(self.items)
            + (self.hidden_shipping_fee() if self.has_hidden_shipping_fee() else 0)
            + self.shipping_charge()
            + self.total_discounts()
        )

    # def total_by_subtotals(self):
    #     return (
    #         self.subtotal + self.tax_charged
    #         + self.shipping_charge + self.total_discounts())

    def set_items(self, items, assert_unmatched=False):
        # Make a new list (to prevent retaining the given list).
        self.items = []
        self.items.extend(items)
        self.items_matched = True
        for i in items:
            if assert_unmatched:
                assert not i.matched
            i.matched = True
            i.charge = self

    def total_quantity(self):
        return sum([i.quantity for i in self.items])

    def order_id(self):
        return self.items[0].order_id

    def order_status(self):
        return self.items[0].order_status

    def ship_status(self):
        return self.items[0].shipment_status

    def website(self):
        return self.items[0].website

    def ship_address(self):
        return self.items[0].shipping_address

    def payment_instrument_types(self):
        return set([pit for i in self.items for pit in i.payment_instrument_type])

    def order_dates(self):
        return [date for items in self.items for date in items.order_date]

    def ship_dates(self):
        return [date for items in self.items for date in items.ship_date]

    def unique_order_dates(self):
        return list(set([d.date() for d in self.order_dates()]))

    def unique_ship_dates(self):
        return list(set([d.date() for d in self.ship_dates()]))

    def subtotal(self):
        return Item.sum_subtotals(self.items)

    def tax(self):
        return Item.sum_subtotals_tax(self.items)

    def total(self):
        """This should be = subtotal + tax."""
        return Item.sum_totals(self.items)

    def shipping_charge(self):
        return sum([i.shipping_charge for i in self.items])

    def total_discounts(self):
        return sum([i.total_discounts for i in self.items])

    def total_owed(self):
        """This should be = total + shipping_charge + total_discounts."""
        return sum([i.total_owed for i in self.items])

    def tracking_numbers(self):
        return list(set([items.tracking for items in self.items]))

    def transact_date(self):
        """The latest ship date in local time zone."""
        dates = [d for i in self.items if i.ship_date for d in i.ship_date]
        if not dates:
            return None
        # Use the local timezone (report has them in UTC).
        # UTC will cause matching to be incorrect.
        return max(dates).astimezone().date()

        # if self.items[0].ship_date and self.items[0].ship_date[0]:
        #
        #     return self.items[0].ship_date[0].astimezone().date()

    def transact_amount(self) -> MicroUSD:
        if self.has_hidden_shipping_fee():
            return -(self.total_owed() + self.hidden_shipping_fee()).round_to_cent()
        return -self.total_owed()

    def match(self, trans):
        self.matched = True
        self.trans_id = trans.id
        # if self.order_id() in ('112-9523119-2065026', '113-7797306-4423467', '112-5028447-9842607'):
        #     print('A match made in MAT')
        #     print(self)
        #     print(trans)

    def get_notes(self):
        note = (
            f"Amazon order id: {self.order_id()}\n"
            f"Order date: {datetime_list_to_dates_str(self.unique_order_dates())}\n"
            f"Ship date: {datetime_list_to_dates_str(self.unique_ship_dates())}\n"
            f'Tracking: {", ".join(self.tracking_numbers())}\n'
            f"Invoice url: {get_invoice_url(self.order_id())}"
        )
        # Notes max out at 1000 as of 2023/12/31. If at or above the limit, use a simplified note:
        if len(note) >= 1000:
            logger.warn("Truncating note for Amazon Charge due to excessive length")
            note = (
                f"Amazon order id: {self.order_id()}\n"
                f"Invoice url: {get_invoice_url(self.order_id())}"
            )
        return note

    def attribute_subtotal_diff_to_misc_charge(self):
        """Sometimes gift wrapping or other misc charge is captured within 'total_owed' for an item but it doesn't belong there."""
        diff = self.total_owed() - self.total_by_items()
        if diff < MICRO_USD_EPS:
            return False

        adjustments = 0
        for i in self.items:
            item_diff = i.total_owed - i.total_owed_by_parts()
            if item_diff > MICRO_USD_EPS:
                i.total_owed -= item_diff

                adjustment = deepcopy(self.items[0])
                adjustment.product_name = "Misc Charge (Gift wrap, etc)"
                adjustment.category = "Shopping"
                adjustment.quantity = 1
                adjustment.shipping_charge = 0
                adjustment.total_discounts = 0

                adjustment.unit_price = item_diff
                adjustment.shipment_item_subtotal = item_diff
                adjustment.total_owed = item_diff

                adjustment.unit_price_tax = 0
                adjustment.shipment_item_subtotal_tax = 0
                adjustment.unit_price_tax = 0

                self.items.append(adjustment)
                adjustments += 1

        return adjustments > 0

    def attribute_itemized_diff_to_shipping_error(self):
        # Shipping is sometimes wrong. Remove shipping off of items if it is the only thing preventing a clean reconcile.
        if not self.shipping_charge():
            return False

        diff = self.total_by_items() - self.total_owed()
        if diff < MICRO_USD_EPS:
            return False

        # Find an item with a non-zero shipping charge and add on the tax.
        adjustments = 0
        for i in self.items:
            item_diff = i.total_owed_by_parts() - i.total_owed
            if item_diff == i.shipping_charge:
                i.shipping_charge = 0
                adjustments += 1
        return adjustments > 0

    def attribute_itemized_diff_to_item_fractional_tax(self):
        """Correct for a slight mismatch when multiple quantities cause per-item taxes to not add up."""
        if self.total_quantity() < 2:
            return False

        itemized_diff = self.total_owed() - self.total_by_items()
        if abs(itemized_diff) < MICRO_USD_EPS:
            return False

        # Only correct for a maximum amount of rounding errors up to the quantity of items in the charge.
        if itemized_diff < CENT_MICRO_USD * self.total_quantity():
            per_item_tax_adjustment = itemized_diff / self.total_quantity()
            for i in self.items:
                i.unit_price_tax += per_item_tax_adjustment
            return True
        return False

    def to_mint_transactions(self, t, skip_free_shipping=False):
        new_transactions = []

        # More expensive items are always more interesting when it comes to
        # budgeting, so show those first (for both itemized and concatted).
        items = sorted(self.items, key=lambda item: item.unit_price, reverse=True)

        # Itemize line-items:
        for i in items:
            item = t.split(
                amount=-i.total(),
                category_name=t.category.name,
                description=i.get_title(88),
                notes=self.get_notes(),
            )
            new_transactions.append(item)

        if self.has_hidden_shipping_fee():
            ship_fee = t.split(
                amount=-self.hidden_shipping_fee(),
                category_name="Shipping",
                description=self.hidden_shipping_fee_note(),
                notes=self.get_notes(),
            )
            new_transactions.append(ship_fee)

        # Itemize the shipping cost, if any.
        is_free_shipping = (
            self.shipping_charge()
            and self.total_discounts()
            and self.total_discounts() == self.shipping_charge()
        )

        if is_free_shipping and skip_free_shipping:
            return new_transactions

        if self.shipping_charge():
            ship = t.split(
                amount=-self.shipping_charge(),
                category_name="Shipping",
                description="Shipping",
                notes=self.get_notes(),
            )
            new_transactions.append(ship)

        # All promotion(s) as one line-item.
        if self.total_discounts():
            # If there was a promo that matches the shipping cost, it's nearly
            # certainly a Free One-day/same-day/etc promo. In this case,
            # categorize the promo instead as 'Shipping', which will cancel out
            # in Mint trends.

            # Note: Since the move to Amazon Request My Data, same/next day
            # shipping that was e.g. $2.99 and then comp'd is no longer
            # included in these reports (discounts and shipping are both zero'd
            # out).
            cat = "Shipping" if is_free_shipping else category.DEFAULT_CATEGORY
            promo = t.split(
                amount=-self.total_discounts(),
                category_name=cat,
                description="Promotion(s)",
                notes=self.get_notes(),
            )
            new_transactions.append(promo)

        return new_transactions

    @classmethod
    def merge(cls, charges):
        if len(charges) == 1:
            return charges[0]
            # result.set_items(Item.merge(result.items))
            # return result

        return Charge([i for c in charges for i in c.items])
        # result = deepcopy(charges[0])
        # result.set_items(Item.merge([i for o in charges for i in o.items]))
        # # for key in ORDER_MERGE_FIELDS:
        # #     result.__dict__[key] = sum([o.__dict__[key] for o in charges])
        # return result

    def __repr__(self):
        return (
            f"Charge ({self.order_id()}): {self.ship_dates() or self.order_dates()}"
            f" Total {str(self.total_owed())}\t"
            f" Total by part {str(self.total_by_items())}\t"
            f"Subtotal {str(self.subtotal())}\t"
            f"Tax {str(self.tax())}\t"
            f"Promo {str(self.total_discounts())}\t"
            f"Ship {str(self.shipping_charge())}\t"
            f"Items: \n{pformat(self.items)}"
        )


MULTI_VALUE_SPLIT = " and "


def parse_optional(value: str) -> Optional[str]:
    if value == "Not Available":
        return None
    return value


class Item:
    """A charge comprises of one or more Items with one or more quantity.

    The general formula for an item is:
    REVISE!!!
    shipment_item_subtotal = unit_price * quantity + other items in same charge
    shipment_item_tax = unit_price_tax * quantity + other items in same charge
    shipment_item_total = shipment_item_subtotal + shipment_item_tax
    total_owed = shipment_item_total + shipping_charge + total_discounts
    """

    # Fields in order as they appear in CSV export

    website: str
    order_id: str
    order_date: List[datetime]  # Example: "2023-12-31T00:21:42Z"
    purchase_order_number: Optional[str]
    currency: str
    unit_price: MicroUSD
    unit_price_tax: MicroUSD
    shipping_charge: MicroUSD
    total_discounts: MicroUSD
    total_owed: MicroUSD
    shipment_item_subtotal: Optional[MicroUSD]
    shipment_item_subtotal_tax: Optional[MicroUSD]
    asin: str
    product_condition: str
    quantity: int
    payment_instrument_type: List[str]
    order_status: str
    shipment_status: Optional[str]
    ship_date: List[datetime]  # "2023-12-31T10:21:37Z"
    shipping_option: str  # Example: "expd-consolidated-us"
    shipping_address: str
    billing_address: str
    carrier_name_and_tracking_number: List[str]  # Example: "AMZN_US(TBA310866232294)"
    product_name: str
    gift_message: Optional[str]
    gift_sender_name: Optional[str]
    gift_recipient_contact_details: Optional[str]

    def __init__(
        self,
        website,
        order_id,
        order_date,
        purchase_order_number,
        currency,
        unit_price,
        unit_price_tax,
        shipping_charge,
        total_discounts,
        total_owed,
        shipment_item_subtotal,
        shipment_item_subtotal_tax,
        asin,
        product_condition,
        quantity,
        payment_instrument_type,
        order_status,
        shipment_status,
        ship_date,
        shipping_option,
        shipping_address,
        billing_address,
        carrier_name_and_tracking_number,
        product_name,
        gift_message,
        gift_sender_name,
        gift_recipient_contact_details,
    ):
        self.website = website
        self.order_id = order_id
        self.order_date = [
            parse_amazon_date(d)
            for d in order_date.split(MULTI_VALUE_SPLIT)
            if parse_amazon_date(d)
        ]  # type: ignore
        self.purchase_order_number = (
            None
            if purchase_order_number == "Not Applicable"
            else parse_optional(purchase_order_number)
        )
        self.currency = currency
        self.unit_price = MicroUSD.parse(unit_price)
        self.unit_price_tax = MicroUSD.parse(unit_price_tax)
        self.shipping_charge = MicroUSD.parse(shipping_charge)
        self.total_discounts = MicroUSD.parse(total_discounts)
        self.total_owed = MicroUSD.parse(total_owed)
        shipment_item_subtotal = parse_optional(shipment_item_subtotal)
        shipment_item_subtotal_tax = parse_optional(shipment_item_subtotal_tax)
        self.shipment_item_subtotal = (
            MicroUSD.parse(shipment_item_subtotal)
            if parse_optional(shipment_item_subtotal)
            else None
        )
        self.shipment_item_subtotal_tax = (
            MicroUSD.parse(shipment_item_subtotal_tax)
            if parse_optional(shipment_item_subtotal_tax)
            else None
        )
        self.asin = asin
        self.product_condition = product_condition
        self.quantity = int(quantity)
        self.payment_instrument_type = payment_instrument_type.split(MULTI_VALUE_SPLIT)
        self.order_status = order_status
        self.shipment_status = parse_optional(shipment_status)
        self.ship_date = []
        for d in ship_date.split(MULTI_VALUE_SPLIT):
            d = parse_optional(d)
            if d:
                parsed_date = parse_amazon_date(d)
                self.ship_date.append(parsed_date)
        self.shipping_option = shipping_option
        self.shipping_address = shipping_address
        self.billing_address = billing_address
        self.carrier_name_and_tracking_number = carrier_name_and_tracking_number.split(
            MULTI_VALUE_SPLIT
        )
        self.product_name = product_name
        self.gift_message = parse_optional(gift_message)
        self.gift_sender_name = parse_optional(gift_sender_name)
        self.gift_recipient_contact_details = parse_optional(
            gift_recipient_contact_details
        )

    @classmethod
    def parse_from_csv(cls, csv_file, progress_factory=no_progress_factory):
        return parse_from_csv_common(
            cls, csv_file, "Parsing Amazon Items", progress_factory
        )

    @staticmethod
    def sum_subtotals(items):
        return sum([i.subtotal() for i in items])

    @staticmethod
    def sum_subtotals_tax(items):
        return sum([i.subtotal_tax() for i in items])

    @staticmethod
    def sum_totals(items):
        return sum([i.total() for i in items])

    def subtotal(self):
        return self.unit_price * self.quantity

    def subtotal_tax(self):
        return self.unit_price_tax * self.quantity

    def total(self):
        """Prior to shipping_charge and total_discounts."""
        return self.subtotal() + self.subtotal_tax()

    def total_owed_by_parts(self):
        """Prior to shipping_charge and total_discounts."""
        return self.total() + self.total_discounts + self.shipping_charge

    # def adjust_unit_tax_based_on_total_owed(self):
    #     """
    #     Returns true if per unit taxes are fractionally adjusted to align total_owed with per unit prices.

    #     This happens when quantity is greater than one and is illustrated as:
    #       total_owed != quantity * (unit_price + unit_price_tax) + total_discounts + shipping_charge
    #     unit_price_tax is rounded, which causes a mismatch when devining total_owed from per-unit prices.
    #     """
    #     subtotal = self.total_owed - self.total_discounts - self.shipping_charge
    #     per_unit_subtotal = self.total()
    #     if subtotal == per_unit_subtotal:
    #         return False

    #     print(subtotal)
    #     print(per_unit_subtotal)
    #     print(self.__dict__)
    #     # exit()
    #     #     # TODO: Adjust tax to be fractional - more precise. Do this for all of the original charges (prior to merge). Adjust per-item amounts such that the total_owed always works out (Within reason - ie 2 cents?)
    #     # if i.total() + i.total_discounts != i.total_owed:
    #     #     print(i.total() + i.total_discounts)
    #     #     print(i.total_owed)
    #     #     print(i)

    # 9990000 + 1010000

    def tax_rate(self) -> float:
        return round(
            self.unit_price_tax.to_float() * 100.0 / self.unit_price.to_float(), 1
        )

    def get_title(self, target_length=100) -> str:
        return get_title(self, target_length)

    def is_cancelled(self) -> bool:
        return self.order_status == "Cancelled"

    def __repr__(self):
        return (
            f"{self.quantity} of Item: "
            f"Order ID {self.order_id}\t"
            f"Status {self.order_status}\t"
            f"Ship Status {self.shipment_status}\t"
            f"Order Date {self.order_date}\t"
            f"Ship Date {self.ship_date}\t"
            f"Tracking {self.carrier_name_and_tracking_number}\t"
            f"Unit Price {str(self.unit_price)}\t"
            f"Unit Tax {str(self.unit_price_tax)}\t"
            f"Total Owed {str(self.total_owed)}\t"
            f"Shipping Charge {str(self.shipping_charge)}\t"
            f"Discounts {str(self.total_discounts)}\t"
            f"{self.product_name}"
        )


# class Refund:
#     matched = False
#     trans_id = None
#     is_refund = True

#     def __init__(self, raw_dict):
#         # Refunds are rad: AMZN doesn't total the tax + sub-total for you.
#         fields = pythonify_amazon_dict(raw_dict)
#         fields['total_refund_amount'] = (
#             fields['refund_amount'] + fields['refund_tax_amount'])
#         self.__dict__.update(fields)

#     @staticmethod
#     def sum_total_refunds(refunds):
#         return sum([r.total_refund_amount for r in refunds])

#     @classmethod
#     def parse_from_csv(cls, csv_file, progress_factory=no_progress_factory):
#         return parse_from_csv_common(
#             cls, csv_file, 'Parsing Amazon Refunds', progress_factory)

#     def match(self, trans):
#         self.matched = True
#         self.trans_id = trans.id

#     def transact_date(self):
#         return self.refund_date

#     def transact_amount(self):
#         return self.total_refund_amount

#     def get_title(self, target_length=100):
#         return get_title(self, target_length)

#     def get_notes(self):
#         return (
#             f'Amazon refund for order id: {self.order_id}\n'
#             f'Buyer: {self.buyer_name}\n'
#             f'Order date: {self.order_date}\n'
#             f'Refund date: {self.refund_date}\n'
#             f'Refund reason: {self.refund_reason}\n'
#             f'Invoice url: {get_invoice_url(self.order_id)}')

#     def to_mint_transaction(self, t):
#         # Refunds have a positive amount.
#         result = t.split(
#             description=self.get_title(88),
#             category_name=category.DEFAULT_MINT_RETURN_CATEGORY,
#             amount=self.total_refund_amount,
#             notes=self.get_notes())
#         return result

#     @staticmethod
#     def merge(refunds):
#         """Collapses identical items by using quantity."""
#         if len(refunds) <= 1:
#             return refunds
#         unique_refund_items = defaultdict(list)
#         for r in refunds:
#             key = (
#                 f'{r.refund_date}-{r.refund_reason}-{r.title}-'
#                 f'{r.total_refund_amount}-{r.asin_isbn}')
#             unique_refund_items[key].append(r)
#         results = []
#         for same_items in unique_refund_items.values():
#             qty = sum([i.quantity for i in same_items])
#             if qty == 1:
#                 results.extend(same_items)
#                 continue

#             refund = same_items[0]
#             refund.quantity = qty
#             refund.total_refund_amount *= qty
#             refund.refund_amount *= qty
#             refund.refund_tax_amount *= qty

#             results.append(refund)
#         return results

#     def __repr__(self):
#         return (
#             f'{self.quantity} of Refund: '
#             f'Total {micro_usd_to_usd_string(self.total_refund_amount)}\t'
#             f'Subtotal {micro_usd_to_usd_string(self.refund_amount)}\t'
#             f'Tax {micro_usd_to_usd_string(self.refund_tax_amount)} '
#             f'{self.title}')
