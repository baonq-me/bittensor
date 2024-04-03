# The MIT License (MIT)
# Copyright © 2021 Yuma Rao
# Copyright © 2023 Opentensor Foundation

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import bittensor

import torch
import logging
from rich.prompt import Confirm
from typing import Union, Tuple, List, Optional
import bittensor.utils.weight_utils as weight_utils
from bittensor.btlogging.defines import BITTENSOR_LOGGER_NAME
from retry import retry

logger = logging.getLogger(BITTENSOR_LOGGER_NAME)


def _do_set_weights(
    subtensor: "bittensor.subtensor",
    wallet: "bittensor.wallet",
    uids: List[int],
    vals: List[int],
    netuid: int,
    version_key: int = bittensor.__version_as_int__,
    wait_for_inclusion: bool = False,
    wait_for_finalization: bool = False,
) -> Tuple[bool, Optional[str]]:  # (success, error_message)
    """
    Internal method to send a transaction to the Bittensor blockchain, setting weights
    for specified neurons. This method constructs and submits the transaction, handling
    retries and blockchain communication.

    Args:
        wallet (bittensor.wallet): The wallet associated with the neuron setting the weights.
        uids (List[int]): List of neuron UIDs for which weights are being set.
        vals (List[int]): List of weight values corresponding to each UID.
        netuid (int): Unique identifier for the network.
        version_key (int, optional): Version key for compatibility with the network.
        wait_for_inclusion (bool, optional): Waits for the transaction to be included in a block.
        wait_for_finalization (bool, optional): Waits for the transaction to be finalized on the blockchain.

    Returns:
        Tuple[bool, Optional[str]]: A tuple containing a success flag and an optional error message.

    This method is vital for the dynamic weighting mechanism in Bittensor, where neurons adjust their
    trust in other neurons based on observed performance and contributions.
    """

    @retry(delay=2, tries=3, backoff=2, max_delay=4)
    def make_substrate_call_with_retry():
        with subtensor.substrate as substrate:
            call = substrate.compose_call(
                call_module="SubtensorModule",
                call_function="set_weights",
                call_params={
                    "dests": uids,
                    "weights": vals,
                    "netuid": netuid,
                    "version_key": version_key,
                },
            )
            # Period dictates how long the extrinsic will stay as part of waiting pool
            extrinsic = substrate.create_signed_extrinsic(
                call=call,
                keypair=wallet.hotkey,
                era={"period": 5},
            )
            response = substrate.submit_extrinsic(
                extrinsic,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
            )
            # We only wait here if we expect finalization.
            if not wait_for_finalization and not wait_for_inclusion:
                return True, "Not waiting for finalziation or inclusion."

            response.process_events()
            if response.is_success:
                return True, "Successfully set weights."
            else:
                return False, response.error_message

    return make_substrate_call_with_retry()


def set_weights_extrinsic(
    subtensor: "bittensor.subtensor",
    wallet: "bittensor.wallet",
    netuid: int,
    uids: Union[torch.LongTensor, list],
    weights: Union[torch.FloatTensor, list],
    version_key: int = 0,
    wait_for_inclusion: bool = False,
    wait_for_finalization: bool = False,
    prompt: bool = False,
) -> Tuple[bool, str]:
    r"""Sets the given weights and values on chain for wallet hotkey account.

    Args:
        subtensor_endpoint (bittensor.subtensor):
            Subtensor endpoint to use.
        wallet (bittensor.wallet):
            Bittensor wallet object.
        netuid (int):
            The ``netuid`` of the subnet to set weights for.
        uids (Union[torch.LongTensor, list]):
            The ``uint64`` uids of destination neurons.
        weights ( Union[torch.FloatTensor, list]):
            The weights to set. These must be ``float`` s and correspond to the passed ``uid`` s.
        version_key (int):
            The version key of the validator.
        wait_for_inclusion (bool):
            If set, waits for the extrinsic to enter a block before returning ``true``, or returns ``false`` if the extrinsic fails to enter the block within the timeout.
        wait_for_finalization (bool):
            If set, waits for the extrinsic to be finalized on the chain before returning ``true``, or returns ``false`` if the extrinsic fails to be finalized within the timeout.
        prompt (bool):
            If ``true``, the call waits for confirmation from the user before proceeding.
    Returns:
        success (bool):
            Flag is ``true`` if extrinsic was finalized or uncluded in the block. If we did not wait for finalization / inclusion, the response is ``true``.
    """

    # First convert types.
    if isinstance(uids, list):
        uids = torch.tensor(uids, dtype=torch.int64)
    if isinstance(weights, list):
        weights = torch.tensor(weights, dtype=torch.float32)

    # Reformat and normalize.
    weight_uids, weight_vals = weight_utils.convert_weights_and_uids_for_emit(
        uids, weights
    )

    # Ask before moving on.
    if prompt:
        if not Confirm.ask(
            "Do you want to set weights:\n[bold white]  weights: {}\n  uids: {}[/bold white ]?".format(
                [float(v / 65535) for v in weight_vals], weight_uids
            )
        ):
            return False, "Prompt refused."

    with bittensor.__console__.status(
        ":satellite: Setting weights on [white]{}[/white] ...".format(subtensor.network)
    ):
        try:
            success, error_message = _do_set_weights(
                subtensor=subtensor,
                wallet=wallet,
                netuid=netuid,
                uids=weight_uids,
                vals=weight_vals,
                version_key=version_key,
                wait_for_finalization=wait_for_finalization,
                wait_for_inclusion=wait_for_inclusion,
            )

            if not wait_for_finalization and not wait_for_inclusion:
                return True, "Not waiting for finalization or inclusion."

            if success == True:
                bittensor.__console__.print(
                    ":white_heavy_check_mark: [green]Finalized[/green]"
                )
                bittensor.logging.success(
                    prefix="Set weights",
                    sufix="<green>Finalized: </green>" + str(success),
                )
                return True, "Successfully set weights and Finalized."
            else:
                bittensor.__console__.print(
                    ":cross_mark: [red]Failed[/red]: error:{}".format(error_message)
                )
                bittensor.logging.warning(
                    prefix="Set weights",
                    sufix="<red>Failed: </red>" + str(error_message),
                )
                return False, error_message

        except Exception as e:
            # TODO( devs ): lets remove all of the bittensor.__console__ calls and replace with loguru.
            bittensor.__console__.print(
                ":cross_mark: [red]Failed[/red]: error:{}".format(e)
            )
            bittensor.logging.warning(
                prefix="Set weights", sufix="<red>Failed: </red>" + str(e)
            )
            return False, str(e)
