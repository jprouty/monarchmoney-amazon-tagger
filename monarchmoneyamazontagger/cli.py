#!/usr/bin/env python3

# This script fetches Amazon "Order History Reports" and annotates your Monarch Money
# transactions based on actual items in each purchase. It can handle charges
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

import argparse
import atexit
from collections import defaultdict
import getpass
import logging
import os
from signal import signal, SIGINT
import time

from outdated import check_outdated

from monarchmoneyamazontagger import amazon
from monarchmoneyamazontagger import mm
from monarchmoneyamazontagger import tagger
from monarchmoneyamazontagger import VERSION
from monarchmoneyamazontagger.args import define_cli_args, TAGGER_BASE_PATH
from monarchmoneyamazontagger.my_progress import (
    counter_progress_cli,
    determinate_progress_cli,
    indeterminate_progress_cli,
)
from monarchmoneyamazontagger.mmclient import MonarchMoneyClient

logger = logging.getLogger(__name__)


def main():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logging.StreamHandler())
    # Disable noisy log spam from filelock from within tldextract.
    logging.getLogger("filelock").setLevel(logging.WARN)

    # For helping remote debugging, also log to file.
    # Developers should be vigilant to NOT log any PII, ever (including being
    # mindful of what exceptions might be thrown).
    log_directory = os.path.join(TAGGER_BASE_PATH, "Tagger Logs")
    os.makedirs(log_directory, exist_ok=True)
    log_filename = os.path.join(
        log_directory, f'{time.strftime("%Y-%m-%d_%H-%M-%S")}.log'
    )
    file_handler = logging.FileHandler(log_filename)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    logger.info(f"Running version {VERSION}")
    # try:
    #     is_outdated, latest_version = check_outdated(
    #         "monarchmoney-amazon-tagger", VERSION
    #     )
    #     if is_outdated:
    #         logger.warning(
    #             "Please update your version by running:\n"
    #             "pip3 install monarchmoney-amazon-tagger --upgrade\n\n"
    #         )
    # except ValueError:
    #     logger.error(f"Version {VERSION} is newer than PyPY version")

    parser = argparse.ArgumentParser(
        description="Tag Monarch Money transactions based on itemized Amazon history."
    )
    define_cli_args(parser)
    args = parser.parse_args()

    if args.version:
        print(f"monarchmoney-amazon-tagger {VERSION}\nBy: Jeff Prouty")
        exit(0)

    mmc = MonarchMoneyClient(args)

    if not args.amazon_export:
        logger.critical("One or more Amazon Export Zip files required.")
        exit(1)

    if args.dry_run:
        logger.info("\nDry Run; no modifications being sent to Monarch Money.\n")

    def on_critical(msg):
        logger.critical(msg)
        exit(1)

    maybe_prompt_for_credentials(args)
    results = tagger.create_updates(
        args,
        mmc,
        on_critical=on_critical,
        indeterminate_progress_factory=indeterminate_progress_cli,
        determinate_progress_factory=determinate_progress_cli,
        counter_progress_factory=counter_progress_cli,
    )

    if not results.success:
        logger.critical("Uncaught error from create_updates. Exiting")
        exit(1)

    log_amazon_stats(results.items, results.charges)  # , results.refunds)
    log_processing_stats(results.stats)

    if args.print_unmatched and results.unmatched_charges:
        logger.warning(
            "The following were not matched to Monarch Money transactions:\n"
        )
        by_oid = defaultdict(list)
        for uo in results.unmatched_charges:
            by_oid[uo.order_id()].append(uo)

        for unmatched_by_oid in by_oid.values():
            c = amazon.Charge.merge(unmatched_by_oid)
            print_unmatched(c)

    if not results.updates:
        logger.info("All done; no new tags to be updated at this point in time!")
        exit(0)

    if args.dry_run:
        logger.info("Dry run. Following are proposed changes:")
        if args.skip_dry_print:
            logger.info("Dry run print results skipped!")
        else:
            tagger.print_dry_run(
                results.updates, ignore_category=args.no_tag_categories
            )
    else:
        num_updates = mmc.send_updates(
            results.updates,
            progress=determinate_progress_cli(
                "Updating Monarch Money", max=len(results.updates)
            ),
            ignore_category=args.no_tag_categories,
        )

        logger.info(f"Sent {num_updates} updates to Monarch Money")


def maybe_prompt_for_credentials(args):
    if not args.mm_email and not args.use_json_backup:
        args.mm_email = input("Money Monarch email: ")
    if not args.mm_password and not args.use_json_backup:
        args.mm_password = getpass.getpass("Money Monarch password: ")


def log_amazon_stats(items, charges):
    logger.info("\nAmazon Stats:")
    if len(charges) == 0 or len(items) == 0:
        logger.info("\tThere were not Amazon charges/items!")
        return
    oid = set([i.order_id for i in items])
    logger.info(
        f'\n{len(oid)} total Amazon orders\n{len(charges)} payment "charges"\n{sum([i.quantity for i in items])} total items ordered'
    )

    first_order_date = min([o.transact_date() for o in charges if o.transact_date()])
    last_order_date = max([o.transact_date() for o in charges if o.transact_date()])
    logger.info(f"Charges ranging from {first_order_date} to {last_order_date}")

    per_item_totals = [i.total() for i in items]
    per_order_totals = [c.total_owed() for c in charges]

    logger.info(f"{str(sum(per_order_totals))} total spend")

    logger.info(
        f"{str(sum(per_order_totals) / len(oid))} avg "
        f"order total (range: {str(min(per_order_totals))}"
        f" - {str(max(per_order_totals))})"
    )
    logger.info(
        f"{str(sum(per_item_totals) / len(items))} avg "
        f"item price (range: {str(min(per_item_totals))}"
        f" - {str(max(per_item_totals))})"
    )

    # if refunds:
    #     first_refund_date = min(
    #         [r.refund_date for r in refunds if r.refund_date])
    #     last_refund_date = max(
    #         [r.refund_date for r in refunds if r.refund_date])
    #     logger.info(
    #         f'\n{len(refunds)} refunds dating from '
    #         f'{first_refund_date} to {last_refund_date}')

    #     per_refund_totals = [r.total_refund_amount for r in refunds]

    #     logger.info(
    #         f'{str(sum(per_refund_totals))} '
    #         'total refunded')


def log_processing_stats(stats):
    # 'Refunds matched w/ transactions: {refund_match} (unmatched refunds: '
    # '{refund_unmatch})\n'

    logger.info(
        "\n{trans} Monarch Money transactions from {earliest_transaction_date} to {latest_transaction_date}\n"
        'Transactions w/ "Amazon" in description: {amazon_in_desc}\n'
        "Transactions ignored: is pending: {pending}\n"
        "\n"
        "charges matched w/ transactions: {order_match} (unmatched charges: "
        "{order_unmatch})\n"
        "Transactions matched w/ charges: {trans_match} (unmatched: "
        "{trans_unmatch})\n"
        "\n"
        "charges skipped: not shipped: {skipped_charges_unshipped}\n"
        "charges skipped: gift card used: {skipped_charges_gift_card}\n"
        "\n"
        "Order fix-up: incorrect tax itemization: {adjust_itemized_tax}\n"
        "Order fix-up: remove erroneous shipping: {rm_shipping_error}\n"
        "Order fix-up: has a misc charges (e.g. gift wrap): {misc_charge}\n"
        "\n"
        "Transactions ignored; already tagged & up to date: "
        "{already_up_to_date}\n"
        "Transactions ignored; ignore retags: {no_retag}\n"
        "Transactions ignored; user skipped retag: {user_skipped_retag}\n"
        "\n"
        "Transactions with personalize categories: {personal_cat}\n"
        "\n"
        "Transactions to be retagged: {retag}\n"
        "Transactions to be newly tagged: {new_tag}\n".format(**stats)
    )


def print_unmatched(amzn_obj):
    proposed_desc = mm.summarize_title(
        [i.get_title() for i in amzn_obj.items], f"{amzn_obj.website()}: "
    )
    logger.warning(proposed_desc)
    logger.warning(
        "\t{}\t{}\t{}".format(
            amzn_obj.transact_date() if amzn_obj.transact_date() else "Never shipped!",
            str(amzn_obj.transact_amount()),
            amazon.get_invoice_url(amzn_obj.order_id()),
        )
    )
    logger.warning("")


if __name__ == "__main__":
    main()
