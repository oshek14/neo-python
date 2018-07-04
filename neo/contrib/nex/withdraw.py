from neo.Core.Blockchain import Blockchain
from neo.Core.TX.Transaction import TransactionOutput, ContractTransaction
from neo.Network.NodeLeader import NodeLeader
from neo.SmartContract.ContractParameterContext import ContractParametersContext
from neo.SmartContract.Contract import Contract
from neo.Prompt.Utils import get_asset_id, lookup_addr_str
from neo.Wallets.Coin import CoinReference
from neocore.UInt160 import UInt160
from neocore.UInt256 import UInt256
from neocore.Fixed8 import Fixed8
from prompt_toolkit import prompt
import json


def PerformWithdrawTx(wallet, tx, contract_hash, prompt_password):

    requestor_contract = wallet.GetDefaultContract()
    if not isinstance(contract_hash, UInt160):
        contract_hash = UInt160.ParseString(contract_hash)
    withdraw_contract_state = Blockchain.Default().GetContract(contract_hash)

    reedeem_script = withdraw_contract_state.Code.Script.hex()

    # there has to be at least 1 param, and the first
    # one needs to be a signature param
    param_list = bytearray(b'\x00')

    # if there's more than one param
    # we set the first parameter to be the signature param
    if len(withdraw_contract_state.Code.ParameterList) > 1:
        param_list = bytearray(withdraw_contract_state.Code.ParameterList)
        param_list[0] = 0

    verification_contract = Contract.Create(reedeem_script, param_list, requestor_contract.PublicKeyHash)

    context = ContractParametersContext(tx)
    context.Add(verification_contract, 0, bytearray(0))

    if context.Completed:

        tx.scripts = context.GetScripts()

        print("withdraw tx %s " % json.dumps(tx.ToJson(), indent=4))

        if prompt_password:
            passwd = prompt("[Password]> ", is_password=True)

            if not wallet.ValidatePassword(passwd):
                print("incorrect password")
                return False

        relayed = NodeLeader.Instance().Relay(tx)

        if relayed:
            # wallet.SaveTransaction(tx) # dont save this tx
            print("Relayed Withdrawal Tx: %s " % tx.Hash.ToString())
            return tx
        else:
            print("Could not relay witdrawal tx %s " % tx.Hash.ToString())
    else:

        print("Incomplete signature")
    return False


def SendVINFromContract(wallet, args):
    if not len(args) == 6:
        raise Exception("Incorrect args length. Format is asset to-addr from-addr amount txhash output_index")

    asset = get_asset_id(wallet, args[0])
    to_addr = lookup_addr_str(wallet, args[1])
    from_addr = lookup_addr_str(wallet, args[2])
    amount = Fixed8.TryParse(args[3], require_positive=True)
    txid = UInt256.ParseString(args[4])
    output_index = int(args[5])

    use_vins_for_asset = [[CoinReference(prev_hash=txid, prev_index=output_index)], asset]

    output = TransactionOutput(AssetId=asset, Value=amount, script_hash=to_addr)
    tx = ContractTransaction(outputs=[output])

    withdraw_tx = wallet.MakeTransaction(tx=tx,
                                         change_address=from_addr,
                                         fee=Fixed8.Zero(),
                                         from_addr=from_addr,
                                         use_standard=False,
                                         watch_only_val=64,
                                         use_vins_for_asset=use_vins_for_asset)

    PerformWithdrawTx(wallet, withdraw_tx, from_addr, True)
