import datetime
import json
import logging
import os
import time
import typing

from monarchmoney import MonarchMoney

logger = logging.getLogger(__name__)


class MonarchMoneyClient:
    args = None
    mm = None
    # To signify a successful user login when args.mm_user_will_login is present.
    user_login_success = False

    def __init__(self, args):
        self.args = args

    def hasValidCredentialsForLogin(self):
        return self.args.mm_email and self.args.mm_password

    def is_logged_in(self):
        return self.mm is not None

    async def login(self):
        if self.is_logged_in():
            return True
        if not self.hasValidCredentialsForLogin():
            logger.error("Missing Monarch Money email or password.")
            return False

        self.mm = MonarchMoney()
        await self.mm.login(self.args.mm_email, self.args.mm_password)
        if self.args.mm_wait_for_sync:
            await self.mm.request_accounts_refresh_and_wait(
                account_ids=self.args.mm_account_ids
            )

        return True

    async def get_transactions(
        self,
        from_date: typing.Optional[datetime.date] = None,
        to_date: typing.Optional[datetime.date] = None,
    ):
        if self.args.use_json_backup:
            json_path = _json_transactions_path(
                self.args.mm_json_backup_path, self.args.use_json_backup
            )
            if not os.path.exists(json_path):
                raise Exception(f"JSON backup file not found: {json_path}")
            logger.info(f"Loading Transactions from json file: {json_path}")
            with open(json_path, "r") as json_in:
                results = json.load(json_in)
                return results

        if not await self.login():
            logger.error("Cannot login")
            return []
        logger.info(
            f"Getting all Monarch Money transactions since {from_date} to {to_date}."
        )

        limit = 100
        offset = 0
        start_date = None
        if from_date:
            start_date = from_date.strftime("%Y-%m-%d")
        end_date = None
        if to_date:
            end_date = to_date.strftime("%Y-%m-%d")

        response = await self.mm.get_transactions(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            account_ids=self.args.mm_account_ids or [],
        )
        results = []
        while True:
            if (
                not response
                or response["allTransactions"]["totalCount"] == 0
                or not response["allTransactions"]["results"]
            ):
                return results
            num_transactions = len(response["allTransactions"]["results"])
            total_count = len(response["allTransactions"]["totalCount"])
            logger.info(f"Received {num_transactions} transactions.")
            logger.info(f"Total of {total_count} transactions.")
            results.extend(response["allTransactions"]["results"])
            offset += num_transactions
            if offset == total_count:
                break
            response = await self.mm.get_transactions(
                limit=limit,
                offset=offset,
                start_date=start_date,
                end_date=end_date,
                account_ids=self.args.mm_account_ids or [],
            )

        if self.args.save_json_backup:
            json_path = _json_transactions_path(
                self.args.mm_json_backup_path, int(time.time())
            )
            logger.info(f"Saving Transactions to json file: {json_path}")
            with open(json_path, "w") as json_out:
                json.dump(results, json_out)

        return results

    async def get_categories(self):
        if self.args.use_json_backup:
            json_path = _json_categories_path(
                self.args.mm_json_backup_path, self.args.use_json_backup
            )
            if not os.path.exists(json_path):
                raise Exception(f"JSON backup file not found: {json_path}")
            logger.info(f"Loading Categories from json file: {json_path}")
            with open(json_path, "r") as json_in:
                results = json.load(json_in)
                return results
        if not await self.login():
            logger.error("Cannot login")
            return []
        logger.info("Getting Monarch Money categories.")

        results = []

        if self.args.save_json_backup:
            json_path = _json_categories_path(
                self.args.mm_json_backup_path, int(time.time())
            )
            logger.info(f"Saving Categories to json file: {json_path}")
            with open(json_path, "w") as json_out:
                json.dump(results, json_out)

        return results

    def send_updates(self, updates, progress, ignore_category: bool = False):
        if not self.login():
            logger.error("Cannot login")
            return 0
        num_requests = 0
        return num_requests
        # for orig_trans, new_trans in updates:
        #     if len(new_trans) == 1:
        #         # Update the existing transaction.
        #         trans = new_trans[0]
        #         modify_trans = {
        #             "type": trans.type,
        #             "description": trans.description,
        #             "notes": trans.notes,
        #         }
        #         if not ignore_category:
        #             modify_trans = {
        #                 **modify_trans,
        #                 "category": {"id": trans.category.id},
        #             }

        #         logger.debug(f'Sending a "modify" transaction request: {modify_trans}')
        #         response = self.webdriver.request(
        #             "PUT",
        #             f"{MINT_TRANSACTIONS}/{trans.id}",
        #             json=modify_trans,
        #             headers=self.get_api_header(),
        #         )
        #         logger.debug(f"Received response: {response.__dict__}")
        #         progress.next()
        #         num_requests += 1
        #     else:
        #         # Split the existing transaction into many.
        #         split_children = []
        #         for trans in new_trans:
        #             category = (
        #                 orig_trans.category if ignore_category else trans.category
        #             )
        #             itemized_split = {
        #                 "amount": f"{micro_usd_to_float_usd(trans.amount)}",
        #                 "description": trans.description,
        #                 "category": {"id": category.id, "name": category.name},
        #                 "notes": trans.notes,
        #             }
        #             split_children.append(itemized_split)

        #         split_edit = {
        #             "type": orig_trans.type,
        #             "amount": micro_usd_to_float_usd(orig_trans.amount),
        #             "splitData": {"children": split_children},
        #         }
        #         logger.debug(f'Sending a "split" transaction request: {split_edit}')
        #         response = self.webdriver.request(
        #             "PUT",
        #             f"{MINT_TRANSACTIONS}/{trans.id}",
        #             json=split_edit,
        #             headers=self.get_api_header(),
        #         )
        #         logger.debug(f"Received response: {response.__dict__}")
        #         progress.next()
        #         num_requests += 1

        # progress.finish()
        # return num_requests


def _json_transactions_path(prefix: str, time_epoch: int):
    return os.path.join(prefix, f"{time_epoch} Transactions.json")


def _json_categories_path(prefix: str, time_epoch: int):
    return os.path.join(prefix, f"{time_epoch} Categories.json")
