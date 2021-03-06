import lib.arg, lib.conn
import lib.config
from lib.segment import Segment
import socket
import lib.segment as segment
import binascii
import fcntl



class Client:
    def __init__(self):
        args = {
            "port" : (int, "Client port"),
            "path" : (str, "Destination path"),
            "-f"   : (None, "Show segment information"),
            "-d"   : (None, "Show full payload in hexadecimal")
        }
        parser = lib.arg.ArgParser("Client", args)
        args   = parser.get_parsed_args()

        self.ip                    = lib.config.CLIENT_BIND_IP
        self.port                  = args.port
        self.path                  = args.path
        self.verbose_segment_print = args.f
        self.show_payload          = args.d
        self.get_metadata          = lib.config.SEND_METADATA
        self.conn                  = lib.conn.UDP_Conn(
            self.ip,
            self.port,
            auto_ifname=lib.config.CLIENT_INTERFACE_NAME,
            send_broadcast=True
            )
        self.ip                    = self.conn.get_ipv4()
        self.server_broadcast_addr = (self.conn.get_broadcast_addr(), lib.config.CLIENT_SEND_PORT)
        self.listen_timeout        = lib.config.CLIENT_LISTEN_TIMEOUT
        self.listen_shake_timeout  = lib.config.CLIENT_LISTEN_HANDSHAKE_TIMEOUT

    def __output_segment_info(self, addr : (str, int), data : "Segment"):
        if self.verbose_segment_print:
            addr_str = f"{addr[0]}:{addr[1]}"
            print(f"[S] [{addr_str}] Segment information :")
            print(data)

        if self.show_payload:
            print(f"[S] [{addr_str}] Payload in hexadecimal")
            print(binascii.hexlify(data.get_payload(), " "))

        if self.verbose_segment_print or self.show_payload:
            print("")


    def __get_metadata(self):
        addr_str = f"{self.server_addr[0]}:{self.server_addr[1]}"
        print(f"\n[Bonus] [{addr_str}] Fetching metadata...")
        try:
            addr, resp, checksum_success = self.conn.listen_single_datagram()
            if checksum_success:
                payload = resp.get_payload()
                # Payload parsing
                parsing_filename = True
                filename         = ""
                file_ext         = ""
                for byte in payload:
                    if byte == 0x4:
                        parsing_filename = False
                    elif parsing_filename:
                        filename += chr(byte)
                    else:
                        file_ext += chr(byte)

                print(f"[Bonus] [{addr_str}] Metadata information :")
                print(f"[Bonus] [{addr_str}] Source filename : {filename}")
                print(f"[Bonus] [{addr_str}] File extension  : {file_ext}\n")
            else:
                print(f"[Bonus] [{addr_str}] Checksum failed, metadata packet is corrupted")
            self.__output_segment_info(addr, resp)
        except socket.timeout:
            print(f"[Bonus] [{addr_str}] Listen timeout, skipping metadata...")



    def __send_ack_reply(self, ack_num : int):
        if ack_num >= 0:
            print(f"[!] [{self.server_addr[0]}:{self.server_addr[1]}] Sending ACK {ack_num}...")
            ack_resp = Segment()
            ack_resp.set_flag([segment.ACK_FLAG])
            ack_resp.set_header({"sequence" : 0, "ack" : ack_num})
            self.conn.send_data(ack_resp, self.server_addr)
        else:
            # Edge case
            pass


    def three_way_handshake(self):
        # 1. SYN to server
        print(f"Client started at {self.ip}:{self.port}")
        print("[!] Initiating three way handshake...")
        print(f"[!] Sending broadcast SYN request to port {self.server_broadcast_addr[1]}")
        syn_req = Segment()
        syn_req.set_flag([segment.SYN_FLAG])
        self.conn.send_data(syn_req, self.server_broadcast_addr)

        # 2. Waiting SYN + ACK from server
        print("[!] Waiting for response...")
        self.conn.set_listen_timeout(self.listen_shake_timeout)
        try:
            server_addr, resp, checksum_success = self.conn.listen_single_datagram()
            if not checksum_success:
                print("[!] Checksum failed")
                exit(1)
            print(f"[S] Getting response from {server_addr[0]}:{server_addr[1]}")
            self.__output_segment_info(server_addr, resp)

            resp_flag = resp.get_flag()
            if resp_flag.syn and resp_flag.ack:
                # 3. Sending ACK to server
                ack_req = Segment()
                ack_req.set_flag([segment.ACK_FLAG])
                self.conn.send_data(ack_req, server_addr)
                self.server_addr = server_addr
                print(f"\n[!] Handshake with {server_addr[0]}:{server_addr[1]} success")
            else:
                print("\n[!] Invalid response : Server SYN-ACK handshake response invalid")
                print(f"[!] Handshake with {server_addr[0]}:{server_addr[1]} failed")
                print(f"[!] Exiting...")
                exit(1)
        except socket.timeout:
            print("\n[!] SYN-ACK response timeout, exiting...")
            exit(1)


    def listen_file_transfer(self):
        print("[!] Starting file transfer...")
        if self.get_metadata:
            self.__get_metadata()

        self.conn.set_listen_timeout(self.listen_timeout)
        with open(self.path, "wb") as dst:
            request_number = 0
            end_of_file    = False
            while not end_of_file:
                try:
                    addr, resp, checksum_success = self.conn.listen_single_datagram()
                    addr_str = f"{addr[0]}:{addr[1]}"
                    if addr == self.server_addr and checksum_success:
                        segment_seq_number = resp.get_header()["sequence"]
                        if segment_seq_number == request_number:
                            print(f"[!] [{addr_str}] Sequence number match with Rn, sending Ack number {request_number}...")
                            dst.write(resp.get_payload())
                            self.__send_ack_reply(request_number)
                            request_number += 1

                        elif resp.get_flag().fin:
                            end_of_file = True
                            print(f"[!] [{addr_str}] FIN flag, stopping transfer...")
                            print(f"[!] [{addr_str}] Sending ACK tearing down connection...")
                            ack_resp = Segment()
                            ack_resp.set_flag([segment.ACK_FLAG])
                            self.conn.send_data(ack_resp, self.server_addr)

                        else:
                            print(f"[!] [{addr_str}] Sequence number not equal with Rn ({segment_seq_number} =/= {request_number}), ignoring...")

                    elif not checksum_success:
                        print(f"[!] [{addr_str}] Checksum failed, ignoring segment")

                    self.__output_segment_info(addr, resp)

                except socket.timeout:
                    print(f"[!] [{self.server_addr[0]}:{self.server_addr[1]}] Listening timeout, resending ACK {request_number-1}...")
                    self.__send_ack_reply(request_number - 1)




        self.conn.close_socket()




if __name__ == '__main__':
    main = Client()
    main.three_way_handshake()
    main.listen_file_transfer()
