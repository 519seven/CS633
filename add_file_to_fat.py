#!/usr/bin/env python3

import binascii
from shutil import copyfile
import argparse
import calendar
import time
import socket
import struct
import sys
import os


class MyImage(object):
    def __init__(self, input_file, output_file, index_number, filename, cluster_ranges, keep_image, debug, extreme_debug):
        # "offset" will indicate number of bytes into a location
        # "size" indicates the total number of bytes large an object is
        self.keep_image = keep_image
        self.debug = debug
        self.extreme_debug = extreme_debug
        # FAT12 structure
        self.sector_size = 512
        self.max_sectors = 2880
        self.fat1_offset = 1
        self.fat2_offset = 10
        self.root_directory_offset = 19        
        self.directory_index_size = 32
        self.filename_offset = 0
        self.starting_cluster_offset = 26
        self.file_size_offset = 28
        # File things
        self.input_file = input_file
        self.output_file = self.AssignOutputFilename(output_file)
        self.index_number = self.GetAvailableDirectoryIndex()
        self.filename, self.extension = self.EightByteFilename(filename)
        self.filename_bytes = bytes(self.filename, 'ascii')
        self.extension_bytes = bytes(self.extension, 'ascii')
        self.cluster_ranges = cluster_ranges.replace(' ','').split(',')
        self.cluster_list = []
        # The range that is supplied by the user must all fit between low and high
        self.low_data_cluster = 2
        self.high_data_cluster = self.max_sectors-31

    def AssignOutputFilename(self, output_file):
        """ Check that we have a value, if not make it name.timestamp """
        if output_file is None:
            gmt = time.gmtime()
            ts = calendar.timegm(gmt)
            return f"{self.input_file}.{ts}"
        return output_file

    def CreateList(self, bottom_range, top_range):
        """ Create a list from a range of numbers """
        print(f"Creating a list from {bottom_range} to {top_range}")
        cluster_list = [item for item in range(bottom_range, top_range+1)]
        print(f"cluster_list: {cluster_list}")
        return cluster_list

    def EightByteFilename(self, filename):
        """ Make the name fit into 8 bytes """
        nameandext = filename.split(".")
        filename = (nameandext[0][:7] + "~") if len(nameandext[0]) > 8 else nameandext[0]
        extension = (nameandext[1][:3])
        return filename, extension

    def GetAvailableDirectoryIndex(self):
        """ Open input_file and see find the next available index """
        # The index of the directory_index starts at 1
        directory_index = 1
        # Find next empty index slot
        # Go 33 sectors in, then every 32 bytes, check the 26th offset for a value
        # Open rb to prevent data alteration at this point
        with open(self.input_file, "rb") as fh:
            while directory_index <= 224:
                # directory_index-1 will be zero the first time through
                # because as soon as we seek to the offset, we're already in the first directory location
                seeker = (self.root_directory_offset*self.sector_size)+((directory_index-1)*self.directory_index_size)+(self.starting_cluster_offset-1)
                fh.seek(seeker)
                if fh.read(2) == b'\x00\x00':
                    return directory_index
                directory_index += 1
        print("root directory is full?")
        return -1

    def IsEntryHighOrLow(self, cluster_number):
        """ Based on page 7 of scan24, we need to know if we're dealing with Entry1 or Entry2 """
        byte_number = cluster_number * (3/2) + 512
        if byte_number.is_integer():
            seeker = int(byte_number)
            return True, False, seeker
        elif not byte_number.is_integer():
            seeker = int(byte_number)-1
            return False, True, seeker

    def ValidClusterRanges(self):
        """ Let's make sure these cluster ranges are legit """
        for cluster_range in self.cluster_ranges:
            the_range = cluster_range.split("-")
            print(f"Checking that range {the_range} falls within our data area")
            try:
                if int(the_range[0]) < self.low_data_cluster or int(the_range[1]) > self.high_data_cluster:
                    print(f"False. {the_range[0]} or {the_range[1]} is outside of our data area")
                    return False
            except TypeError as t_err:
                print(f"Error. Range does not appear to be an int")
                return False
        return True

    def WriteFilename(self):
        """ Write filename to root directory index """
        print(f"Copying {self.input_file} to {self.output_file}")
        copyfile(self.input_file, self.output_file)
        # Open r+b to open as binary for writing to
        with open(self.output_file, "r+b") as fh:
            seeker = (self.root_directory_offset*self.sector_size)+((self.index_number-1)*self.directory_index_size)
            # Convert to little-endian
            f_array = bytearray()
            print(f"Reversing {self.filename}")
            f_array.extend(map(ord, self.filename))
            #f_array.reverse()
            print(f"f_array is {f_array}")
            print(f"Preparing to write {f_array} to {seeker}")
            fh.seek(seeker)
            fh.write(f_array)
            e_array = bytearray()
            print(f"Reversing {self.extension}")
            e_array.extend(map(ord, self.extension))
            #e_array.reverse()
            print(f"e_array is {e_array}")
            print(f"Preparing to write {e_array} to {seeker}")
            fh.seek(seeker+8)
            fh.write(e_array)
        print("Filename and extension written to root directory")
        return True

    def WriteFileSize(self):
        """ Write size of file to root directory """
        # Simply a calculation of the number of clusters (e.g. sectors) * 512
        total_size = 0
        for cluster_range in self.cluster_ranges:
            clusters = cluster_range.split("-")
            difference = int(clusters[1]) - int(clusters[0]) + 1
            self.cluster_list.extend(self.CreateList(int(clusters[0]), int(clusters[1])))
            print(f"Cluster difference between {clusters[1]} and {clusters[0]} is {difference}")
            total_size += difference*512
        print(f"Total size has been calculated as {total_size}")
        with open(self.output_file, "r+b") as fh:
            seeker = (self.root_directory_offset*self.sector_size)+((self.index_number-1)*self.directory_index_size)+(self.file_size_offset)
            #s_array = bytearray()
            print(f"Reversing {total_size}")
            ba_size = (total_size).to_bytes(4, byteorder='little')
            print(f"Preparing to write {ba_size} to {seeker}")
            fh.seek(seeker)
            fh.write(ba_size)
        print("File size written to root directory")
        return True

    def WriteClustersToImage(self):
        """ Write clusters to FAT table """
        # Use the array we built earlier
        print(f"Writing the following list of clusters to FAT structure: {self.cluster_list}")
        padding = 3
        with open(self.output_file, "r+b") as fh:
            # The first cluster goes into offset 26 (2 Bytes) in root directory
            seeker = (self.root_directory_offset*self.sector_size)+((self.index_number-1)*self.directory_index_size)+(self.starting_cluster_offset)
            # Convert first item in list to two bytes
            first_address = (self.cluster_list[0]).to_bytes(2, byteorder='little')
            print(f"If I were me, I'd write {first_address} to {seeker}")
            fh.seek(seeker)
            fh.write(first_address)
            # Now, the rest are written to FAT area
            for i, item in enumerate(self.cluster_list):
                # If Entry 1 then the byte calculation returned a whole number
                # If Entry 2 then the byte calculation returned a half number
                # This item determines where we write the data
                entry1, entry2, seeker = self.IsEntryHighOrLow(item)
                # The data we are writing is the next item
                if i+1 >= len(self.cluster_list):
                    next_item = 4095
                else:
                    next_item = self.cluster_list[i+1]
                # If we're at the end of the list then write 0xfff
                print(f"Ready to perform calculations on {next_item} (hex:{hex(next_item)}) [entry1={entry1}; entry2={entry2}, seeker={seeker}]")
                fh.seek(seeker)
                my_bytes = b'\x00'+fh.read(3)
                if self.debug:
                    print(f"bytes from disk image: {my_bytes}")
                unpacked_bytes, = struct.unpack('>I', bytes(my_bytes))
                if self.debug:
                    print(type(unpacked_bytes), unpacked_bytes)
                nstr = str(hex(unpacked_bytes)).replace('0x', '').zfill(6)
                le_three_bytes = "".join(map(str.__add__, nstr[-2::-2] ,nstr[-1::-2]))
                if self.debug:
                    print(f"Existing values: unpacked_bytes:{hex(unpacked_bytes)}|nstr:{nstr}|(le)three_bytes:{le_three_bytes}|Entry1={le_three_bytes[-3:]}|Entry2={le_three_bytes[:3]}")
                if entry1:
                    # We need to deal with entry1 (see page 7 of scan24 paper)
                    if self.debug:
                        print("Updating entry1")
                    entry1_bytes = hex(next_item)[2:].zfill(3)
                    entry2_bytes = le_three_bytes[:3]
                else:
                    if self.debug:
                        print("Updating entry2")
                    entry1_bytes = le_three_bytes[-3:]
                    entry2_bytes = hex(next_item)[2:].zfill(3)
                new_entry = f"{entry2_bytes}{entry1_bytes}"
                if self.debug:
                    print(f"new_entry: {new_entry}")
                packed_bytes = struct.pack('<I', int(new_entry, 16))
                if self.debug:
                    print(f"Writing packed_bytes ({packed_bytes[:-1]}) to {seeker}")
                fh.seek(seeker)
                fh.write(packed_bytes[:-1])
        print(f"{self.filename}.{self.extension} written to root directory index #{self.index_number}")
        return True

    
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', dest='input_file',
        help='Name of binary input image file',
        required=True)
    parser.add_argument('-o', '--ouput', dest='output_file', 
        help='Name of binary output image file',
        required=False)
    parser.add_argument('-n', '--index-number', dest='index_number', 
        help='Directory index number; default is "next free location"', 
        required=False)
    parser.add_argument('-f', '--filename', dest='filename',
        help="Name of file you are adding to the directory",
        required=False,
        default='noname.ext')
    parser.add_argument('-c', '--clusters', dest='cluster_ranges', 
        help='Provide a quoted comma-delimited range; e.g "33-143,1300-1704,2388-2779"',
        required=True)
    parser.add_argument('-r', '--retain', dest='keep_image', 
        help='After the run, you are asked to keep the image or remove it. Pass this in to keep the image.',
        action='store_true',
        required=False)
    parser.add_argument('-d', '--debug', dest='debug', 
        help='Enable verbose logging to stdout',
        action='store_true',
        default=False)
    parser.add_argument('-e', '--extra-debug', dest='extreme_debug', 
        help='Enable extra-verbosity in the debugging process (replace bytes with known values, etc.)',
        action='store_true',
        default=False)

    args = parser.parse_args()

    args_dict = vars(args)
    img = MyImage(**args_dict)
    if img.debug:
        print(f"{img.filename}.{img.extension} [{img.filename_bytes} and {img.extension_bytes}] will be saved at index #{img.index_number}")
    else:
        print(f"Saving {img.filename}.{img.extension} to directory index #{img.index_number}")
    while True:
        write_file = input("Would you like to continue? [y/n] ")
        if write_file.upper() != "Y" and write_file.upper() != "N":
            continue
        elif write_file.upper() == "Y":
            # Add the file to the directory
            print("Saving file to directory!")
            if img.ValidClusterRanges():
                if not img.WriteFilename():
                    print(f"Failed to write filename")
                    print("Exiting...")
                    sys.exit(1)
                if not img.WriteFileSize():
                    print(f"Failed to write file size")
                    print("Exiting...")
                    sys.exit(1)
                if not img.WriteClustersToImage():
                    print(f"Failed to write clusters")
                    print("Exiting...")
                    sys.exit(1)
            else:
                print("Cluster ranges are not valid. Exiting...")
                sys.exit(1)
            break
        elif write_file.upper() == "N":
            print("Not saving data. Exiting!")
            sys.exit(1)
    if img.debug:
        print("Debug mode detected.")
        while True and not img.keep_image:
            remove_file = input("Would you like me to remove the output file? [y/n] ")
            if remove_file.upper() == "Y":
                print("Removing output file")
                os.remove(img.output_file)
                break
            elif remove_file.upper() == "N":
                print("Not removing output file")
                break
        if img.keep_image:
            print("-r passed in. Not removing output file.")
    print("DONE!")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted")
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)