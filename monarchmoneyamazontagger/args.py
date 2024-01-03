import argparse
import os

TAGGER_BASE_PATH = os.path.join(os.path.expanduser("~"), 'MintAmazonTagger')


def get_name_to_help_dict(parser):
    return dict([(a.dest, a.help) for a in parser._actions])


def define_common_args(parser):
    """Parseargs shared between both CLI & GUI programs."""
    # Amazon Input, as zip file:
    parser.add_argument(
        '--amazon_export', nargs='+', type=argparse.FileType('r', encoding='utf-8'),
        help=('One or more Amazon Data Exports zip file (type either "Orders" or "All Data").'))

    # Monarch Money creds:
    parser.add_argument(
        '--mm_email', default=None,
        help=('Monarch Money e-mail address for login.'))
    parser.add_argument(
        '--mm_password', default=None,
        help=('Monarch Money password for login.'))

    # Prefix customization:
    parser.add_argument(
        '--description_prefix_override', type=str,
        help=('The prefix to use when updating the description for each Mint '
              'transaction. By default, the \'Website\' value from the Amazon '
              'Data Export is used. If a string is provided, use '
              'this instead for all matched transactions. If given, this is '
              'used in conjunction with amazon_domains to detect if a '
              'transaction has already been tagged by this tool.'))
    parser.add_argument(
        '--description_return_prefix_override', type=str,
        help=('The prefix to use when updating the description for each Mint '
              'refund. By default, the \'Website\' value from the Amazon '
              'Data Export is used with refund appended (e.g. '
              '\'Amazon.com Refund: ...\'. If a string is provided here, use '
              'this instead for all matched refunds. If given, this is '
              'used in conjunction with amazon_domains to detect if a '
              'refund has already been tagged by this tool.'))
    parser.add_argument(
        '--amazon_domains', type=str,
        # From: https://en.wikipedia.org/wiki/Amazon_(company)#Website
        default=('amazon.com,amazon.cn,amazon.in,amazon.co.jp,amazon.com.sg,'
                 'amazon.com.tr,amazon.fr,amazon.de,amazon.it,amazon.nl,'
                 'amazon.es,amazon.co.uk,amazon.ca,amazon.com.mx,'
                 'amazon.com.au,amazon.com.br'),
        help=('A list of all valid Amazon domains/websites. These should '
              'match the website column from the Amazon Data Export and is used to '
              'detect if a transaction has already been tagged by this tool.'))

    # To itemize or not to itemize; that is the question:
    parser.add_argument(
        '--verbose_itemize', action='store_true',
        help=('Itemize everything, instead of the default behavior, which is '
              'to not itemize out shipping/promos/etc if '
              'there is only one item per Monarch Money transaction. Will also remove '
              'free shipping.'))
    parser.add_argument(
        '--no_itemize', action='store_true',
        help=('Do not split Monarch Money transactions into individual items with '
              'attempted categorization.'))

    parser.add_argument(
        '--num_updates', type=int,
        default=0,
        help=('Only send the first N updates to Monarch Money (or print N updates at '
              'dry run). If not present, all updates are sent or printed.'))
    parser.add_argument(
        '--retag_changed', action='store_true',
        help=('For transactions that have been previously tagged by this '
              'script, override any edits (like adjusting the category). This '
              'feature works by looking for "Amazon.com: " at the start of a '
              'transaction. If the user changes the description, then the '
              'tagger won\'t know to leave it alone.'))

    parser.add_argument(
        '--max_unmatched_charges_combinations', type=int,
        default=20,
        help=('Maximum number of charges to attempt to combinatorically match with '
              'transactions. The current implementation is pretty memory intensive, '
              'so setting this higher will potentially consume all available memory, '
              'and cause the tagger to take a while.'))
    # Tagging options:
    parser.add_argument(
        '--no_tag_categories', action='store_true',
        help=('Do not update Monarch Money categories. This is useful as '
              'Amazon doesn\'t provide the best categorization and it is '
              'pretty common user behavior to manually change the categories. '
              'This flag prevents tagger from wiping out that user work.'))
    parser.add_argument(
        '--do_not_predict_categories', action='store_true',
        help=('Do not attempt to predict custom category tagging based on any '
              'tagging overrides. By default (no arg) tagger will attempt to '
              'find items that you have manually changed categories for.'))
    parser.add_argument(
        '--max_days_between_payment_and_shipping', type=int,
        default=5,
        help=('How many days are allowed to pass between when Amazon has '
              'shipped an order and when the payment has posted to your '
              'bank account (as per Monarch Money\'s view).'))
    parser.add_argument(
        '--mm_input_description_filter', type=str,
        default='amazon,amzn',
        help=('Only consider Monarch Money transactions that have one of these strings '
              'in the description field. Case-insensitive comma-separated.'))
    parser.add_argument(
        '--mm_input_include_user_description', action='store_true',
        help=('Consider using the current description from Monarch Money when '
              'determining if a transaction is an Amazon purchase. This will '
              'include any user edits or previous runs of MonarchMoneyAmazonTagger. '
              'This is similar to --mm_input_include_inferred_description.'))
    # TODO(jprouty): Revisit if this is still accurate in the FIData message.
    parser.add_argument(
        '--mm_input_include_inferred_description', action='store_true',
        help=('Consider using the inferred description from Monarch Money\'s '
              '"FinancialInstitutionData" when determining if '
              'a transaction is an Amazon purchase. This may be necessary '
              'when a bank renames transactions to "Debit card payment". '
              'Monarch Money sometimes auto-recovers these into "Amazon", and flipping '
              'this flag will help match these. To know if you should use it, '
              'find a transaction in the Monarch Money tool, and click on the details. '
              'Look for "Appears on your BANK ACCOUNT NAME statement as NOT '
              'USEFUL NAME on DATE".'))
    parser.add_argument(
        '--mm_input_categories_filter', type=str,
        help=('Only consider Monarch Money transactions that match one of '
              'the given categories here. Comma separated list of Mint '
              'categories.'))

    parser.add_argument(
        '--save_json_backup', action='store_true',
        default=False,
        help=('Saves a backup of your Monarch Money transactions to a json file, '
              'just in case anything goes wrong or for rapid '
              'development so you don\'t have to download from Monarch Money every '
              'time the tool is run. Off by default to prevent storing '
              'sensitive information locally without a user knowing it.'))
    parser.add_argument(
        '--use_json_backup', type=int,
        help=('Do not fetch categories or transactions from Monarch Money. Use json '
              'backups from the given epoch instead. If coupled with --dry_run, no '
              'connection to Monarch Money is established.'))
    default_json_path = os.path.join(TAGGER_BASE_PATH, 'Monarch Money Backup')
    parser.add_argument(
        '--mm_json_backup_path', type=str,
        default=default_json_path,
        help='Where to store the Monarch Money backup json files.')


def define_gui_args(parser):
    define_common_args(parser)

    # TODO: Clean up and remove.
    parser.add_argument(
        '--prompt_retag',
        default=False,
        action='store_false',
        help=('Unsupported for gui; but must be defined to false.'))


def define_cli_args(parser):
    define_common_args(parser)

    # Debugging/testing.
    parser.add_argument(
        '--dry_run', action='store_true',
        help=('Do not modify Mint transaction; instead print the proposed '
              'changes to console.'))
    parser.add_argument(
        '--skip_dry_print', action='store_true',
        help=('Do not print dry run results (useful for development).'))

    parser.add_argument(
        '-V', '--version', action='store_true',
        help='Shows the app version and quits.')

    # Retag transactions that have already been tagged previously:
    parser.add_argument(
        '--prompt_retag', action='store_true',
        help=('For transactions that have been previously tagged by this '
              'script, override any edits (like adjusting the category) but '
              'only after confirming each change. More gentle than '
              '--retag_changed'))
    parser.add_argument(
        '--print_unmatched', action='store_true',
        help=('At completion, print unmatched charges to help manual tagging.'))
