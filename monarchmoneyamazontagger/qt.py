from collections import defaultdict
import operator

from PyQt6.QtCore import Qt, QAbstractTableModel, QUrl  # type: ignore
from PyQt6.QtGui import QDesktopServices  # type: ignore
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
)  # type: ignore

from monarchmoneyamazontagger import amazon
from monarchmoneyamazontagger import mm


class MMUpdatesTableModel(QAbstractTableModel):
    def __init__(self, updates, **kwargs):
        super(MMUpdatesTableModel, self).__init__(**kwargs)
        self.my_data = []
        for i, update in enumerate(updates):
            orig_trans, new_trans = update

            descriptions = []
            category_names = []
            amounts = []

            if orig_trans.children:
                for trans in orig_trans.children:
                    descriptions.append("CURRENTLY: " + trans.description)
                    category_names.append(trans.category.name)
                    amounts.append(str(trans.amount))
            else:
                descriptions.append("CURRENTLY: " + orig_trans.description)
                category_names.append(orig_trans.category.name)
                amounts.append(str(orig_trans.amount))

            if len(new_trans) == 1:
                trans = new_trans[0]
                descriptions.append("PROPOSED: " + trans.description)
                category_names.append(trans.category.name)
                amounts.append(str(trans.amount))
            else:
                for trans in reversed(new_trans):
                    descriptions.append("PROPOSED: " + trans.description)
                    category_names.append(trans.category.name)
                    amounts.append(str(trans.amount))

            self.my_data.append(
                [
                    update,
                    True,
                    orig_trans.date.strftime("%Y/%m/%d"),
                    "\n".join(descriptions),
                    "\n".join(category_names),
                    "\n".join(amounts),
                    orig_trans.charges[0].order_id(),
                ]
            )

        self.header = ["", "Date", "Description", "Category", "Amount", "Amazon Order"]

    def rowCount(self, parent):
        return len(self.my_data)

    def columnCount(self, parent):
        return len(self.header)

    def data(self, index, role):
        if not index.isValid():
            return None
        if index.column() == 0:
            value = "" if self.my_data[index.row()][index.column() + 1] else "Skip"
        else:
            value = self.my_data[index.row()][index.column() + 1]
        if role == Qt.ItemDataRole.EditRole:
            return value
        elif role == Qt.ItemDataRole.DisplayRole:
            return value
        elif role == Qt.ItemDataRole.CheckStateRole:
            if index.column() == 0:
                return (
                    Qt.CheckState.Checked
                    if self.my_data[index.row()][index.column() + 1]
                    else Qt.CheckState.Unchecked
                )

    def headerData(self, col, orientation, role):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.header[col]
        return None

    def sort(self, col, order):
        self.layoutAboutToBeChanged.emit()
        self.my_data = sorted(self.my_data, key=operator.itemgetter(col + 1))
        if order == Qt.SortOrder.DescendingOrder:
            self.my_data.reverse()
        self.layoutChanged.emit()

    def flags(self, index):
        if not index.isValid():
            return None
        if index.column() == 0:
            return (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
        else:
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def setData(self, index, value, role):
        if not index.isValid():
            return False
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            print(self.my_data[index.row()][index.column() + 1])
            print(value)
            print(Qt.CheckState.Checked)
            self.my_data[index.row()][index.column() + 1] = (
                value == Qt.CheckState.Checked.value
            )
            print(self.my_data[index.row()][index.column() + 1])
        self.dataChanged.emit(index, index)
        return True

    def get_selected_updates(self):
        return [d[0] for d in self.my_data if d[1]]


class AmazonUnmatchedTableDialog(QDialog):
    def __init__(self, unmatched_charges, **kwargs):
        super(AmazonUnmatchedTableDialog, self).__init__(**kwargs)
        self.setWindowTitle("Unmatched Amazon charges/Refunds")
        self.setModal(True)
        self.model = AmazonUnmatchedTableModel(unmatched_charges)
        v_layout = QVBoxLayout()
        self.setLayout(v_layout)

        label = QLabel(
            f"Below are the {len(unmatched_charges)} Amazon charges/Refunds "
            "which did not match a Mint transaction."
        )
        v_layout.addWidget(label)

        table = QTableView()
        table.doubleClicked.connect(self.on_double_click)
        table.clicked.connect(self.on_activated)

        def resize():
            table.resizeColumnsToContents()
            table.resizeRowsToContents()
            min_width = sum(table.columnWidth(i) for i in range(5))
            table.setMinimumSize(min_width + 20, 600)

        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setModel(self.model)
        table.setSortingEnabled(True)
        resize()
        self.model.layoutChanged.connect(resize)

        v_layout.addWidget(table)

        close_button = QPushButton("Close")
        v_layout.addWidget(close_button)
        close_button.clicked.connect(self.close)

    def open_amazon_order_id(self, order_id):
        if order_id:
            QDesktopServices.openUrl(QUrl(amazon.get_invoice_url(order_id)))

    def on_activated(self, index):
        # Only handle clicks on the order_id cell.
        if index.column() != 3:
            return
        order_id = self.model.data(index, Qt.ItemDataRole.DisplayRole)
        self.open_amazon_order_id(order_id)

    def on_double_click(self, index):
        if index.column() == 3:
            # Ignore double clicks on the order_id cell.
            return
        order_id_cell = self.model.createIndex(index.row(), 3)
        order_id = self.model.data(order_id_cell, Qt.ItemDataRole.DisplayRole)
        self.open_amazon_order_id(order_id)


class AmazonUnmatchedTableModel(QAbstractTableModel):
    def __init__(self, unmatched_charges, **kwargs):
        super(AmazonUnmatchedTableModel, self).__init__(**kwargs)

        self.header = [
            "Ship Date",
            "Proposed Mint Description",
            "Amount",
            "Order ID",
        ]
        self.my_data = []
        by_oid = defaultdict(list)
        for uo in unmatched_charges:
            by_oid[uo.order_id()].append(uo)
        for unmatched_by_oid in by_oid.values():
            merged = amazon.Charge.merge(unmatched_by_oid)
            self.my_data.append(self._create_row(merged))

    def _create_row(self, amzn_obj):
        proposed_mint_desc = mm.summarize_title(
            [i.get_title() for i in amzn_obj.items], f"{amzn_obj.website()}" f": "
        )
        return [
            amzn_obj.transact_date().strftime("%Y/%m/%d")
            if amzn_obj.transact_date()
            else "Never shipped!",
            proposed_mint_desc,
            str(amzn_obj.transact_amount()),
            amzn_obj.order_id(),
        ]

    def rowCount(self, parent):
        return len(self.my_data)

    def columnCount(self, parent):
        return len(self.header)

    def data(self, index, role):
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            return self.my_data[index.row()][index.column()]

    def headerData(self, col, orientation, role):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.header[col]
        return None

    def sort(self, col, order):
        self.layoutAboutToBeChanged.emit()
        self.my_data = sorted(self.my_data, key=operator.itemgetter(col))
        if order == Qt.SortOrder.DescendingOrder:
            self.my_data.reverse()
        self.layoutChanged.emit()

    def flags(self, index):
        if not index.isValid():
            return None
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def setData(self, index, value, role):
        if not index.isValid():
            return False
        return True


class AmazonStatsDialog(QDialog):
    def __init__(self, items, charges, refunds, **kwargs):
        super(AmazonStatsDialog, self).__init__(**kwargs)
        self.setWindowTitle("Amazon Stats for Items/charges/Refunds")
        self.setModal(True)
        v_layout = QVBoxLayout()
        self.setLayout(v_layout)

        v_layout.addWidget(QLabel("Amazon Stats:"))
        if len(charges) == 0 or len(items) == 0:
            v_layout.addWidget(QLabel("There were not Amazon charges/items!"))

            close_button = QPushButton("Close")
            v_layout.addWidget(close_button)
            close_button.clicked.connect(self.close)
            return

        first_order_date = min([d for c in charges for d in c.order_dates()])
        last_order_date = max([d for c in charges for d in c.order_dates()])

        v_layout.addWidget(
            QLabel(f"charges ranging from {first_order_date} to {last_order_date}")
        )

        per_item_totals = [i.total() for i in items]
        per_order_totals = [c.total_owed() for c in charges]

        v_layout.addWidget(QLabel(f"{str(sum(per_order_totals))} total spend"))
        v_layout.addWidget(
            QLabel(
                f"{str(sum(per_order_totals) / len(charges))} "
                "avg order total (range: "
                f"{str(min(per_order_totals))} - "
                f"{str(max(per_order_totals))})"
            )
        )
        v_layout.addWidget(
            QLabel(
                f"{str(sum(per_item_totals) / len(items))} "
                "avg item price (range: "
                f"{str(min(per_item_totals))} - "
                f"{str(max(per_item_totals))})"
            )
        )

        if refunds:
            first_refund_date = min([r.refund_date for r in refunds if r.refund_date])
            last_refund_date = max([r.refund_date for r in refunds if r.refund_date])
            v_layout.addWidget(
                QLabel(
                    f"\n{len(refunds)} refunds dating from "
                    f"{first_refund_date} to {last_refund_date}"
                )
            )

            per_refund_totals = [r.total_refund_amount for r in refunds]

            v_layout.addWidget(
                QLabel(f"{str(sum(per_refund_totals))} " "total refunded")
            )

        close_button = QPushButton("Close")
        v_layout.addWidget(close_button)
        close_button.clicked.connect(self.close)


class TaggerStatsDialog(QDialog):
    def __init__(self, stats, **kwargs):
        super(TaggerStatsDialog, self).__init__(**kwargs)
        self.setWindowTitle("Tagger Stats")
        self.setModal(True)
        v_layout = QVBoxLayout()
        self.setLayout(v_layout)

        v_layout.addWidget(
            QLabel(
                "\nTransactions: {trans}\n"
                'Transactions w/ "Amazon" in description: {amazon_in_desc}\n'
                "Transactions ignored: is pending: {pending}\n"
                "\n"
                "charges matched w/ transactions: {order_match} (unmatched charges: "
                "{order_unmatch})\n"
                "Transactions matched w/ charges/refunds: {trans_match} "
                "(unmatched: "
                "{trans_unmatch})\n"
                "\n"
                "charges skipped: not shipped: {skipped_charges_unshipped}\n"
                "charges skipped: gift card used: {skipped_charges_gift_card}\n"
                "\n"
                "Order fix-up: incorrect tax itemization: {adjust_itemized_tax}\n"
                "Order fix-up: has a misc charges (e.g. gift wrap): "
                "{misc_charge}\n"
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
        )

        close_button = QPushButton("Close")
        v_layout.addWidget(close_button)
        close_button.clicked.connect(self.close)
