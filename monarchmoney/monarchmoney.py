import calendar
from datetime import datetime
import os
import pickle
import oathtool
import time
from typing import Any, Dict, Optional, List

from aiohttp import ClientSession
from aiohttp.client import DEFAULT_TIMEOUT
import asyncio
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport
from graphql import DocumentNode


AUTH_HEADER_KEY = "authorization"
CSRF_KEY = "csrftoken"
DEFAULT_RECORD_LIMIT = 100
ERRORS_KEY = "error_code"
SESSION_DIR = ".mm"
SESSION_FILE = f"{SESSION_DIR}/mm_session.pickle"


class MonarchMoneyEndpoints(object):
    BASE_URL = "https://api.monarchmoney.com"

    @classmethod
    def getLoginEndpoint(cls) -> str:
        return cls.BASE_URL + "/auth/login/"

    @classmethod
    def getGraphQL(cls) -> str:
        return cls.BASE_URL + "/graphql"


class RequireMFAException(Exception):
    pass


class LoginFailedException(Exception):
    pass


class RequestFailedException(Exception):
    pass


class MonarchMoney(object):
    def __init__(
        self,
        session_file: str = SESSION_FILE,
        timeout: int = 10,
        token: Optional[str] = None,
    ) -> None:
        self._headers = {
            "Client-Platform": "web",
        }
        if token:
            self._headers["Authorization"] = f"Token {token}"

        self._session_file = session_file
        self._token = token
        self._timeout = timeout

    @property
    def timeout(self) -> int:
        """The timeout, in seconds, for GraphQL calls."""
        return self._timeout

    def set_timeout(self, timeout_secs: int) -> None:
        """Sets the default timeout on GraphQL API calls, in seconds."""
        self._timeout = timeout_secs

    @property
    def token(self) -> Optional[str]:
        return self._token

    def set_token(self, token: str) -> None:
        self._token = token

    async def interactive_login(
        self, use_saved_session: bool = True, save_session: bool = True
    ) -> None:
        """Performs an interactive login for iPython and similar environments."""
        email = input("Email: ")
        passwd = input("Password: ")
        try:
            await self.login(email, passwd, use_saved_session, save_session)
        except RequireMFAException:
            await self.multi_factor_authenticate(
                email, passwd, input("Two Factor Code: ")
            )
            if save_session:
                self.save_session(self._session_file)

    async def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        use_saved_session: bool = True,
        save_session: bool = True,
        mfa_secret_key: Optional[str] = None,
    ) -> None:
        """Logs into a Monarch Money account."""
        if use_saved_session and os.path.exists(self._session_file):
            print(f"Using saved session found at {self._session_file}")
            self.load_session(self._session_file)
            return

        if email is None or password is None:
            raise LoginFailedException(
                "Email and password are required to login when not using a saved session."
            )
        await self._login_user(email, password, mfa_secret_key)
        if save_session:
            self.save_session(self._session_file)

    async def multi_factor_authenticate(
        self, email: str, password: str, code: str
    ) -> None:
        """Performs multi-factor authentication to access a Monarch Money account."""
        await self._multi_factor_authenticate(email, password, code)

    async def get_accounts(self) -> Dict[str, Any]:
        """
        Gets the list of accounts configured in the Monarch Money account.
        """
        query = gql(
            """
          query GetAccounts {
            accounts {
              ...AccountFields
              __typename
            }
            householdPreferences {
              id
              accountGroupOrder
              __typename
            }
          }

          fragment AccountFields on Account {
            id
            displayName
            syncDisabled
            deactivatedAt
            isHidden
            isAsset
            mask
            createdAt
            updatedAt
            displayLastUpdatedAt
            currentBalance
            displayBalance
            includeInNetWorth
            hideFromList
            hideTransactionsFromReports
            includeBalanceInNetWorth
            includeInGoalBalance
            dataProvider
            dataProviderAccountId
            isManual
            transactionsCount
            holdingsCount
            manualInvestmentsTrackingMethod
            order
            icon
            logoUrl
            type {
              name
              display
              __typename
            }
            subtype {
              name
              display
              __typename
            }
            credential {
              id
              updateRequired
              disconnectedFromDataProviderAt
              dataProvider
              institution {
                id
                plaidInstitutionId
                name
                status
                logo
                __typename
              }
              __typename
            }
            institution {
              id
              name
              logo
              primaryColor
              url
              __typename
            }
            __typename
          }
        """
        )
        return await self.gql_call(
            operation="GetAccounts",
            graphql_query=query,
        )

    async def request_accounts_refresh(self, account_ids: List[str]) -> bool:
        """
        Requests Monarch to refresh account balances and transactions with
        source institutions.  Returns True if request was successfully started.

        Otherwise, throws a `RequestFailedException`.
        """
        query = gql(
            """
          mutation Common_ForceRefreshAccountsMutation($input: ForceRefreshAccountsInput!) {
            forceRefreshAccounts(input: $input) {
              success
              errors {
                ...PayloadErrorFields
                __typename
              }
              __typename
            }
          }

          fragment PayloadErrorFields on PayloadError {
            fieldErrors {
              field
              messages
              __typename
            }
            message
            code
            __typename
          }
          """
        )

        variables = {
            "input": {
                "accountIds": account_ids,
            },
        }

        response = await self.gql_call(
            operation="Common_ForceRefreshAccountsMutation",
            graphql_query=query,
            variables=variables,
        )

        if not response["forceRefreshAccounts"]["success"]:
            raise RequestFailedException(response["forceRefreshAccounts"]["errors"])

        return True

    async def is_accounts_refresh_complete(self) -> bool:
        """
        Checks on the status of a prior request to refresh account balances.

        Returns:
          - True if refresh request is completed.
          - False if refresh request still in progress.

        Otherwise, throws a `RequestFailedException`.
        """
        query = gql(
            """
          query ForceRefreshAccountsQuery {
            accounts {
              id
              hasSyncInProgress
              __typename
            }
          }
          """
        )

        response = await self.gql_call(
            operation="ForceRefreshAccountsQuery",
            graphql_query=query,
            variables={},
        )

        if "accounts" not in response:
            raise RequestFailedException("Unable to request status of refresh")

        return all([not x["hasSyncInProgress"] for x in response["accounts"]])

    async def request_accounts_refresh_and_wait(
        self,
        account_ids: Optional[List[str]] = None,
        timeout: int = 300,
        delay: int = 10,
    ) -> bool:
        """
        Convenience method for forcing an accounts refresh on Monarch, as well
        as waiting for the refresh to complete.

        Returns True if all accounts are refreshed within the timeout specified, False otherwise.

        :param account_ids: The list of accounts IDs to refresh.
          If set to None, all account IDs will be implicitly fetched.
        :param timeout: The number of seconds to wait for the refresh to complete
        :param delay: The number of seconds to wait for each check on the refresh request
        """
        if account_ids is None:
            account_data = await self.get_accounts()
            account_ids = [x["id"] for x in account_data["accounts"]]
        await self.request_accounts_refresh(account_ids)
        start = time.time()
        refreshed = False
        while not refreshed and (time.time() <= (start + timeout)):
            await asyncio.sleep(delay)
            refreshed = await self.is_accounts_refresh_complete()
        return refreshed

    async def get_account_holdings(self, account_id: int) -> Dict[str, Any]:
        """
        Get the holdings information for a brokerage or similar type of account.
        """
        query = gql(
            """
          query Web_GetHoldings($input: PortfolioInput) {
            portfolio(input: $input) {
              aggregateHoldings {
                edges {
                  node {
                    id
                    quantity
                    basis
                    totalValue
                    securityPriceChangeDollars
                    securityPriceChangePercent
                    lastSyncedAt
                    holdings {
                      id
                      type
                      typeDisplay
                      name
                      ticker
                      closingPrice
                      isManual
                      closingPriceUpdatedAt
                      __typename
                    }
                    security {
                      id
                      name
                      type
                      ticker
                      typeDisplay
                      currentPrice
                      currentPriceUpdatedAt
                      closingPrice
                      closingPriceUpdatedAt
                      oneDayChangePercent
                      oneDayChangeDollars
                      __typename
                    }
                    __typename
                  }
                  __typename
                }
                __typename
              }
              __typename
            }
          }
        """
        )

        variables = {
            "input": {
                "accountIds": [str(account_id)],
                "endDate": datetime.today().strftime("%Y-%m-%d"),
                "includeHiddenHoldings": True,
                "startDate": datetime.today().strftime("%Y-%m-%d"),
            },
        }

        return await self.gql_call(
            operation="Web_GetHoldings",
            graphql_query=query,
            variables=variables,
        )

    async def get_subscription_details(self) -> Dict[str, Any]:
        """
        The type of subscription for the Monarch Money account.
        """
        query = gql(
            """
          query GetSubscriptionDetails {
            subscription {
              id
              paymentSource
              referralCode
              isOnFreeTrial
              hasPremiumEntitlement
              __typename
            }
          }
        """
        )
        return await self.gql_call(
            operation="GetSubscriptionDetails",
            graphql_query=query,
        )

    async def get_transactions(
        self,
        limit: int = DEFAULT_RECORD_LIMIT,
        offset: Optional[int] = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        search: str = "",
        category_ids: List[str] = [],
        account_ids: List[str] = [],
        tag_ids: List[str] = [],
        has_attachments: Optional[bool] = None,
        has_notes: Optional[bool] = None,
        hidden_from_reports: Optional[bool] = None,
        is_split: Optional[bool] = None,
        is_recurring: Optional[bool] = None,
        imported_from_mint: Optional[bool] = None,
        synced_from_institution: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Gets transaction data from the account.

        :param limit: the maximum number of transactions to download, defaults to DEFAULT_RECORD_LIMIT.
        :param offset: the number of transactions to skip (offset) before retrieving results.
        :param start_date: the earliest date to get transactions from, in "yyyy-mm-dd" format.
        :param end_date: the latest date to get transactions from, in "yyyy-mm-dd" format.
        :param search: a string to filter transactions. use empty string for all results.
        :param category_ids: a list of category ids to filter.
        :param account_ids: a list of account ids to filter.
        :param tag_ids: a list of tag ids to filter.
        :param has_attachments: a bool to filter for whether the transactions have attachments.
        :param has_notes: a bool to filter for whether the transactions have notes.
        :param hidden_from_reports: a bool to filter for whether the transactions are hidden from reports.
        :param is_split: a bool to filter for whether the transactions are split.
        :param is_recurring: a bool to filter for whether the transactions are recurring.
        :param imported_from_mint: a bool to filter for whether the transactions were imported from mint.
        :param synced_from_institution: a bool to filter for whether the transactions were synced from an institution.
        """

        query = gql(
            """
          query GetTransactionsList($offset: Int, $limit: Int, $filters: TransactionFilterInput, $orderBy: TransactionOrdering) {
            allTransactions(filters: $filters) {
              totalCount
              results(offset: $offset, limit: $limit, orderBy: $orderBy) {
                id
                ...TransactionOverviewFields
                __typename
              }
              __typename
            }
            transactionRules {
              id
              __typename
            }
          }
    
          fragment TransactionOverviewFields on Transaction {
            id
            amount
            pending
            date
            hideFromReports
            plaidName
            notes
            isRecurring
            reviewStatus
            needsReview
            attachments {
              id
              extension
              filename
              originalAssetUrl
              publicId
              sizeBytes
              __typename
            }
            isSplitTransaction
            createdAt
            updatedAt
            category {
              id
              name
              icon
              __typename
            }
            merchant {
              name
              id
              transactionsCount
              __typename
            }
            account {
              id
              displayName
              __typename
            }
            tags {
              id
              name
              color
              order
              __typename
            }
            __typename
          }
        """
        )

        variables = {
            "offset": offset,
            "limit": limit,
            "orderBy": "date",
            "filters": {
                "search": search,
                "categories": category_ids,
                "accounts": account_ids,
                "tags": tag_ids,
            },
        }

        # If bool filters are not defined (i.e. None), then it should not apply the filter
        if has_attachments is not None:
            variables["filters"]["hasAttachments"] = has_attachments

        if has_notes is not None:
            variables["filters"]["hasNotes"] = has_notes

        if hidden_from_reports is not None:
            variables["filters"]["hideFromReports"] = hidden_from_reports

        if is_recurring is not None:
            variables["filters"]["isRecurring"] = is_recurring

        if is_split is not None:
            variables["filters"]["isSplit"] = is_split

        if imported_from_mint is not None:
            variables["filters"]["importedFromMint"] = imported_from_mint

        if synced_from_institution is not None:
            variables["filters"]["syncedFromInstitution"] = synced_from_institution

        if start_date and end_date:
            variables["filters"]["startDate"] = start_date
            variables["filters"]["endDate"] = end_date
        elif bool(start_date) != bool(end_date):
            raise Exception(
                "You must specify both a startDate and endDate, not just one of them."
            )

        return await self.gql_call(
            operation="GetTransactionsList", graphql_query=query, variables=variables
        )

    async def create_transaction(
        self,
        date: str,
        account_id: str,
        amount: float,
        merchant_name: str,
        category_id: str,
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Creates a transaction with the given parameters
        """
        query = gql(
            """
          mutation Common_CreateTransactionMutation($input: CreateTransactionMutationInput!) {
            createTransaction(input: $input) {
              errors {
                ...PayloadErrorFields
                __typename
              }
              transaction {
                id
              }
              __typename
            }
          }

          fragment PayloadErrorFields on PayloadError {
            fieldErrors {
              field
              messages
              __typename
            }
            message
            code
            __typename
          }
        """
        )

        variables = {
            "input": {
                "date": date,
                "accountId": account_id,
                "amount": round(amount, 2),
                "merchantName": merchant_name,
                "categoryId": category_id,
                "notes": notes,
            }
        }

        return await self.gql_call(
            operation="Common_CreateTransactionMutation",
            graphql_query=query,
            variables=variables,
        )

    async def get_transaction_categories(self) -> Dict[str, Any]:
        """
        Gets all the categories configured in the account.
        """
        query = gql(
            """
          query GetCategories {
            categories {
              ...CategoryFields
              __typename
            }
          }

          fragment CategoryFields on Category {
            id
            order
            name
            icon
            systemCategory
            isSystemCategory
            isDisabled
            updatedAt
            createdAt
            group {
              id
              name
              type
              __typename
            }
            __typename
          }
        """
        )
        return await self.gql_call(operation="GetCategories", graphql_query=query)

    async def get_transaction_category_groups(self) -> Dict[str, Any]:
        """
        Gets all the category groups configured in the account.
        """
        query = gql(
            """
          query ManageGetCategoryGroups {
              categoryGroups {
                  id
                  name
                  order
                  type
                  updatedAt
                  createdAt
                  __typename
              }
          }
        """
        )
        return await self.gql_call(
            operation="ManageGetCategoryGroups", graphql_query=query
        )

    async def get_transaction_tags(self) -> Dict[str, Any]:
        """
        Gets all the tags configured in the account.
        """
        query = gql(
            """
          query GetHouseholdTransactionTags($search: String, $limit: Int, $bulkParams: BulkTransactionDataParams) {
            householdTransactionTags(
              search: $search
              limit: $limit
              bulkParams: $bulkParams
            ) {
              id
              name
              color
              order
              transactionCount
              __typename
            }
          }
        """
        )
        return await self.gql_call(
            operation="GetHouseholdTransactionTags", graphql_query=query
        )

    async def get_transaction_details(
        self, transaction_id: str, redirect_posted: bool = True
    ) -> Dict[str, Any]:
        """
        Returns detailed information about a transaction.

        :param transaction_id: the transaction to fetch.
        :param redirect_posted: whether to redirect posted transactions.
        """
        query = gql(
            """
          query GetTransactionDrawer($id: UUID!, $redirectPosted: Boolean) {
            getTransaction(id: $id, redirectPosted: $redirectPosted) {
              id
              amount
              pending
              isRecurring
              date
              originalDate
              hideFromReports
              needsReview
              reviewedAt
              reviewedByUser {
                id
                name
                __typename
              }
              plaidName
              notes
              hasSplitTransactions
              isSplitTransaction
              isManual
              splitTransactions {
                id
                ...TransactionDrawerSplitMessageFields
                __typename
              }
              originalTransaction {
                id
                ...OriginalTransactionFields
                __typename
              }
              attachments {
                id
                publicId
                extension
                sizeBytes
                filename
                originalAssetUrl
                __typename
              }
              account {
                id
                ...TransactionDrawerAccountSectionFields
                __typename
              }
              category {
                id
                __typename
              }
              goal {
                id
                __typename
              }
              merchant {
                id
                name
                transactionCount
                logoUrl
                recurringTransactionStream {
                  id
                  __typename
                }
                __typename
              }
              tags {
                id
                name
                color
                order
                __typename
              }
              needsReviewByUser {
                id
                __typename
              }
              __typename
            }
            myHousehold {
              users {
                id
                name
                __typename
              }
              __typename
            }
          }

          fragment TransactionDrawerSplitMessageFields on Transaction {
            id
            amount
            merchant {
              id
              name
              __typename
            }
            category {
              id
              icon
              name
              __typename
            }
            __typename
          }

          fragment OriginalTransactionFields on Transaction {
            id
            date
            amount
            merchant {
              id
              name
              __typename
            }
            __typename
          }

          fragment TransactionDrawerAccountSectionFields on Account {
            id
            displayName
            icon
            logoUrl
            id
            mask
            subtype {
              display
              __typename
            }
            __typename
          }
        """
        )

        variables = {
            "id": transaction_id,
            "redirectPosted": redirect_posted,
        }

        return await self.gql_call(
            operation="GetTransactionDrawer", variables=variables, graphql_query=query
        )

    async def get_transaction_splits(self, transaction_id: str) -> Dict[str, Any]:
        """
        Returns the transaction split information for a transaction.

        :param transaction_id: the transaction to query.
        """
        query = gql(
            """
          query TransactionSplitQuery($id: UUID!) {
            getTransaction(id: $id) {
              id
              amount
              category {
                id
                name
                icon
                __typename
              }
              merchant {
                id
                name
                __typename
              }
              splitTransactions {
                id
                merchant {
                  id
                  name
                  __typename
                }
                category {
                  id
                  icon
                  name
                  __typename
                }
                amount
                notes
                __typename
              }
              __typename
            }
          }
        """
        )

        variables = {"id": transaction_id}

        return await self.gql_call(
            operation="TransactionSplitQuery", variables=variables, graphql_query=query
        )

    async def update_transaction_splits(
        self, transaction_id: str, split_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Creates, modifies, or deletes the splits for a given transaction.

        Returns the split information for the updates transaction.

        :param transaction_id: the original transaction to modify.
        :param split_data: the splits to create, modify, or delete.
          If empty list or None is given, all splits will be deleted.
          If split_data is given, all existing splits for transaction_id will be replaced with the new splits.
          split_data takes the format of [{"merchantName": ..., "amount": ..., "categoryId": ...}, split2, split3, ...]
          sum([split.amount for split in split_data]) must equal transaction_id.amount.
        """
        query = gql(
            """
          mutation Common_SplitTransactionMutation($input: UpdateTransactionSplitMutationInput!) {
            updateTransactionSplit(input: $input) {
              errors {
                ...PayloadErrorFields
                __typename
              }
              transaction {
                id
                hasSplitTransactions
                splitTransactions {
                  id
                  merchant {
                    id
                    name
                    __typename
                  }
                  category {
                    id
                    icon
                    name
                    __typename
                  }
                  amount
                  notes
                  __typename
                }
                __typename
              }
              __typename
            }
          }

          fragment PayloadErrorFields on PayloadError {
            fieldErrors {
              field
              messages
              __typename
            }
            message
            code
            __typename
          }
        """
        )

        if split_data is None:
            split_data = []

        variables = {"id": transaction_id, "splitData": split_data}

        return await self.gql_call(
            operation="Common_SplitTransactionMutation",
            variables=variables,
            graphql_query=query,
        )

    async def get_cashflow(
        self,
        limit: int = DEFAULT_RECORD_LIMIT,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Gets all the categories configured in the account.
        """
        query = gql(
            """
          query Web_GetCashFlowPage($filters: TransactionFilterInput) {
            byCategory: aggregates(filters: $filters, groupBy: ["category"]) {
              groupBy {
                category {
                  id
                  name
                  icon
                  group {
                    id
                    type
                    __typename
                  }
                  __typename
                }
                __typename
              }
              summary {
                sum
                __typename
              }
              __typename
            }
            byCategoryGroup: aggregates(filters: $filters, groupBy: ["categoryGroup"]) {
              groupBy {
                categoryGroup {
                  id
                  name
                  type
                  __typename
                }
                __typename
              }
              summary {
                sum
                __typename
              }
              __typename
            }
            byMerchant: aggregates(filters: $filters, groupBy: ["merchant"]) {
              groupBy {
                merchant {
                  id
                  name
                  logoUrl
                  __typename
                }
                __typename
              }
              summary {
                sumIncome
                sumExpense
                __typename
              }
              __typename
            }
            summary: aggregates(filters: $filters, fillEmptyValues: true) {
              summary {
                sumIncome
                sumExpense
                savings
                savingsRate
                __typename
              }
              __typename
            }
          }
        """
        )

        variables = {
            "limit": limit,
            "orderBy": "date",
            "filters": {
                "search": "",
                "categories": [],
                "accounts": [],
                "tags": [],
            },
        }

        if start_date and end_date:
            variables["filters"]["startDate"] = start_date
            variables["filters"]["endDate"] = end_date
        elif (start_date is None) ^ (end_date is None):
            raise Exception(
                "You must specify both a startDate and endDate, not just one of them."
            )
        else:
            current_year = datetime.now().year
            current_month = datetime.now().month
            last_date = calendar.monthrange(current_year, current_month)[1]
            variables["filters"]["startDate"] = datetime(
                current_year, current_month, 1
            ).strftime("%Y-%m-%d")
            variables["filters"]["endDate"] = datetime(
                datetime.now().year, datetime.now().month, last_date
            ).strftime("%Y-%m-%d")

        return await self.gql_call(
            operation="Web_GetCashFlowPage", variables=variables, graphql_query=query
        )

    async def get_cashflow_summary(
        self,
        limit: int = DEFAULT_RECORD_LIMIT,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Gets all the categories configured in the account.
        """
        query = gql(
            """
          query Web_GetCashFlowPage($filters: TransactionFilterInput) {
            summary: aggregates(filters: $filters, fillEmptyValues: true) {
              summary {
                sumIncome
                sumExpense
                savings
                savingsRate
                __typename
              }
              __typename
            }
          }
        """
        )

        variables = {
            "limit": limit,
            "orderBy": "date",
            "filters": {
                "search": "",
                "categories": [],
                "accounts": [],
                "tags": [],
            },
        }

        if start_date and end_date:
            variables["filters"]["startDate"] = start_date
            variables["filters"]["endDate"] = end_date
        elif bool(start_date) != bool(end_date):
            raise Exception(
                "You must specify both a startDate and endDate, not just one of them."
            )
        else:
            current_year = datetime.now().year
            current_month = datetime.now().month
            last_date = calendar.monthrange(current_year, current_month)[1]
            variables["filters"]["startDate"] = datetime(
                current_year, current_month, 1
            ).strftime("%Y-%m-%d")
            variables["filters"]["endDate"] = datetime(
                datetime.now().year, datetime.now().month, last_date
            ).strftime("%Y-%m-%d")

        return await self.gql_call(
            operation="Web_GetCashFlowPage", variables=variables, graphql_query=query
        )

    async def update_transaction(
        self,
        transaction_id: str,
        category_id: Optional[str] = None,
        merchant_name: Optional[str] = None,
        goal_id: Optional[str] = None,
        amount: Optional[float] = None,
        date: Optional[str] = None,
        hide_from_reports: Optional[bool] = None,
        needs_review: Optional[bool] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Updates a single existing transaction as identified by the transaction_id
        The only required attribute is transaction_id. Calling this function with
        only the transaction_id will have no effect on the existing transaction data
        but will not cause an error.

        Comments on parameters:
        - transaction_id: Must match an existing transaction_id returned from Monarch
        - category_id: This parameter is only needed when the user wants to change the
            current category. When provided, it must match an existing category_id returned
            from Monarch. An empty string is equivalent to the parameter not being passed.
        - merchant_name: This parameter is only needed when the user wants to change
            the existing merchant name. Empty strings are ignored by the Monarch API
            when passed since a non-empty merchant name is required for all transactions
        - goal_id: This parameter is only needed when the user wants to change
            the existing goal.  When provided, it must match an existing goal_id returned
            from Monarch.  An empty string can be passed to clear out existing goal associations.
        - amount:  This parameter is only needed when the user wants to update
            the existing transaction amount. Empty strings are explicitly ignored by this code
            to avoid errors in the API.
        - date:  This parameter is only needed when the user wants to update
            the existing transaction date. Empty strings are explicitly ignored by this code
            to avoid errors in the API.  Required format is "2023-10-30"
        - hide_from_reports: This parameter is only needed when the user wants to update the
            existing transaction's hide-from-reports value.  If passed, the parameter is cast to
            Booleans to avoid API issues.
        - needs_review: This parameter is only needed when the user wants to update the
            existing transaction's needs-review value.  If passed, the parameter is cast to
            Booleans to avoid API issues.
        - notes: This parameter is only needed when the user wants to change
            the existing note.  An empty string can be passed to clear out existing notes.

        Examples:
        - To update a note: mm.update_transaction(
            transaction_id="160820461792094418",
            notes="my note")

        - To clear a note: mm.update_transaction(
            transaction_id="160820461792094418",
            notes="")

        - To update all items:
            mm.update_transaction(
                transaction_id="160820461792094418",
                category_id="160185840107743863",
                merchant_name="Amazon",
                goal_id="160826408575920275",
                amount=123.45,
                date="2023-11-09",
                hide_from_reports=False,
                needs_review="ThisWillBeCastToTrue",
                notes=f'Updated On: {datetime.now().strftime("%m/%d/%Y %H:%M:%S")}',
            )
        """
        query = gql(
            """
        mutation Web_TransactionDrawerUpdateTransaction($input: UpdateTransactionMutationInput!) {
            updateTransaction(input: $input) {
            transaction {
                id
                amount
                pending
                date
                hideFromReports
                needsReview
                reviewedAt
                reviewedByUser {
                id
                name
                __typename
                }
                plaidName
                notes
                isRecurring
                category {
                id
                __typename
                }
                goal {
                id
                __typename
                }
                merchant {
                id
                name
                __typename
                }
                __typename
            }
            errors {
                ...PayloadErrorFields
                __typename
            }
            __typename
            }
        }

        fragment PayloadErrorFields on PayloadError {
            fieldErrors {
            field
            messages
            __typename
            }
            message
            code
            __typename
        }
        """
        )

        variables = {
            "input": {
                "id": transaction_id,
            }
        }

        # Within Monarch, these values cannot be empty. Monarch will simply ignore updates
        # to category and merchant name that are empty strings or None.
        # As such, no need to avoid adding to variables
        variables["input"].update({"category": category_id})
        variables["input"].update({"name": merchant_name})

        # Monarch will not accept nulls for amount and date.
        # Don't update values if an empty string is passed or if parameter is None
        if amount:
            variables["input"].update({"amount": amount})
        if date:
            variables["input"].update({"date": date})

        # Don't update values if the parameter is not passed or explicitly set to None.
        # Passed values must be cast to bool to avoid API errors
        if hide_from_reports is not None:
            variables["input"].update({"hideFromReports": bool(hide_from_reports)})
        if needs_review is not None:
            variables["input"].update({"needsReview": bool(needs_review)})

        # We want an empty string to clear the goal and notes parameters but the values should not
        # be cleared if the parameter isn't passed
        # Don't update values if the parameter is not passed or explicitly set to None.
        if goal_id is not None:
            variables["input"].update({"goalId": goal_id})
        if notes is not None:
            variables["input"].update({"notes": notes})

        return await self.gql_call(
            operation="Web_TransactionDrawerUpdateTransaction",
            variables=variables,
            graphql_query=query,
        )

    async def gql_call(
        self,
        operation: str,
        graphql_query: DocumentNode,
        variables: Dict[str, Any] = {},
    ) -> Dict[str, Any]:
        """
        Makes a GraphQL call to Monarch Money's API.
        """
        return await self._get_graphql_client().execute_async(
            document=graphql_query, operation_name=operation, variable_values=variables
        )

    def save_session(self, filename: str) -> None:
        """
        Saves the auth token needed to access a Monarch Money account.
        """
        session_data = {"token": self._token}
        if not os.path.exists(SESSION_DIR):
            os.makedirs(SESSION_DIR)

        with open(filename, "wb") as fh:
            pickle.dump(session_data, fh)

    def load_session(self, filename: str) -> None:
        """
        Loads pre-existing auth token from a Python pickle file.
        """
        with open(filename, "rb") as fh:
            data = pickle.load(fh)
            self.set_token(data["token"])
            self._headers["Authorization"] = f"Token {self._token}"

    async def _login_user(
        self, email: str, password: str, mfa_secret_key: Optional[str]
    ) -> None:
        """
        Performs the initial login to a Monarch Money account.
        """
        data = {
            "password": password,
            "supports_mfa": True,
            "trusted_device": False,
            "username": email,
        }

        if mfa_secret_key:
            data["totp"] = oathtool.generate_otp(mfa_secret_key)

        async with ClientSession(headers=self._headers) as session:
            async with session.post(
                MonarchMoneyEndpoints.getLoginEndpoint(), data=data
            ) as resp:
                if resp.status == 403:
                    raise RequireMFAException("Multi-Factor Auth Required")
                elif resp.status != 200:
                    raise LoginFailedException(
                        f"HTTP Code {resp.status}: {resp.reason}"
                    )

                response = await resp.json()
                self.set_token(response["token"])
                self._headers["Authorization"] = f"Token {self._token}"

    async def _multi_factor_authenticate(
        self, email: str, password: str, code: str
    ) -> None:
        """
        Performs the MFA step of login.
        """
        data = {
            "password": password,
            "supports_mfa": True,
            "totp": code,
            "trusted_device": False,
            "username": email,
        }

        async with ClientSession(headers=self._headers) as session:
            async with session.post(
                MonarchMoneyEndpoints.getLoginEndpoint(), data=data
            ) as resp:
                if resp.status != 200:
                    response = await resp.json()
                    error_message = (
                        response["error_code"]
                        if response is not None
                        else "Unknown error"
                    )
                    raise LoginFailedException(error_message)

                response = await resp.json()
                self.set_token(response["token"])
                self._headers["Authorization"] = f"Token {self._token}"

    def _get_graphql_client(self) -> Client:
        """
        Creates a correctly configured GraphQL client for connecting to Monarch Money.
        """
        if self._headers is None:
            raise LoginFailedException(
                "Make sure you call login() first or provide a session token!"
            )
        transport = AIOHTTPTransport(
            url=MonarchMoneyEndpoints.getGraphQL(),
            headers=self._headers,
            timeout=self._timeout,
        )
        return Client(
            transport=transport,
            fetch_schema_from_transport=False,
            execute_timeout=self._timeout,
        )
