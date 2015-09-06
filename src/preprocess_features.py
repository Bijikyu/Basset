#!/usr/bin/env python
from optparse import OptionParser
import gzip
import os
import subprocess
import sys

import h5py
import numpy as np

################################################################################
# preprocess_features.py
#
# Preprocess a set of feature BED files for Basset analysis, potentially adding
# them to an existing database of features, specified as a BED file with the
# target activities comma-separated in column 4 and a full activity table file.
################################################################################

################################################################################
# main
################################################################################
def main():
    usage = 'usage: %prog [options] <target_beds_file>'
    parser = OptionParser(usage)
    parser.add_option('-a', dest='db_act_file', help='Existing database of activity scores')
    parser.add_option('-b', dest='db_bed', help='Existing database of BED peaks.')
    parser.add_option('-c', dest='chrom_lengths_file', help='Table of chromosome lengths')
    parser.add_option('-m', dest='merge_dist', default=0, type='int', help='Distance under which to merge features. Can be negative. [Default: %default]')
    parser.add_option('-n', dest='no_db_activity', default=False, action='store_true', help='Do not pass along the activities of the database sequences [Default: %default]')
    parser.add_option('-o', dest='out_prefix', default='peaks', help='Output file prefix [Default: %default]')
    parser.add_option('-s', dest='peak_size', default=600, type='int', help='Peak extension size [Default: %default]')
    parser.add_option('-y', dest='ignore_y', default=False, action='store_true', help='Ignore Y chromsosome peaks [Default: %default]')
    (options,args) = parser.parse_args()

    if len(args) != 1:
    	parser.error('Must provide file labeling the targets and providing BED file paths.')
    else:
    	target_beds_file = args[0]

    # determine whether we'll add to an existing DB
    db_targets = []
    db_add = False
    if (options.db_bed and not options.db_act_file) or (not options.db_bed and options.db_act_file):
    	parser.error('Must provide both BED file and activity table if you want to add to an existing database')
    elif options.db_bed and options.db_act_file:
    	db_add = True
        if not options.no_db_activity:
            # read db target names
            db_act_in = open(options.db_act_file)
            db_targets = db_act_in.readline().split('\t')[1:]
            db_act_in.close()

    # read in targets and assign them indexes into the db
    target_beds = []
    target_dbi = []
    for line in open(target_beds_file):
    	a = line.rstrip().split('\t')
    	target_dbi.append(len(db_targets))
    	db_targets.append(a[0])
    	target_beds.append(a[1])

    # read in chromosome lengths
    chrom_lengths = {}
    if options.chrom_lengths_file:
        chrom_lengths = {}
        for line in open(options.chrom_lengths_file):
            a = line.split()
            chrom_lengths[a[0]] = int(a[1])
    else:
        print >> sys.stderr, 'Warning: chromosome lengths not provided, so regions near ends may be incorrect.'

    #################################################################
    # print peaks to chromosome-specific files
    #################################################################
    chrom_files = {}
    chrom_outs = {}

    peak_beds = target_beds
    if db_add:
        peak_beds.append(options.db_bed)

    for bi in range(len(peak_beds)):
        if peak_beds[bi][-3:] == '.gz':
            peak_bed_in = gzip.open(peak_beds[bi])
        else:
            peak_bed_in = open(peak_beds[bi])

        for line in peak_bed_in:
            a = line.split('\t')
            a[-1] = a[-1].rstrip()

            chrom = a[0]
            strand = '+'
            if len(a) > 5 and a[5] in '+-':
                strand = a[5]
            chrom_key = (chrom,strand)

            # open chromosome file
            if chrom_key not in chrom_outs:
                chrom_files[chrom_key] = '%s_%s_%s.bed' % (options.out_prefix, chrom, strand)
                chrom_outs[chrom_key] = open(chrom_files[chrom_key], 'w')

            # if it's the db bed
            if db_add and bi == len(peak_beds)-1:
                if options.no_db_activity:
                    # set activity to null
                    a[6] = '.'
                    print >> chrom_outs[chrom_key], '\t'.join(a[:7])
                else:
                    print >> chrom_outs[chrom_key], line,

            # if it's a new bed
            else:
                # specify the target index
                while len(a) < 7:
                    a.append('')
                a[5] = strand
                a[6] = str(target_dbi[bi])
                print >> chrom_outs[chrom_key], '\t'.join(a[:7])

        peak_bed_in.close()

    # close chromosome-specific files
    for chrom_key in chrom_outs:
        chrom_outs[chrom_key].close()

    # ignore Y
    if options.ignore_y:
        for orient in '+-':
            chrom_key = ('chrY',orient)
            if chrom_key in chrom_files:
                os.remove(chrom_files[chrom_key])
                del chrom_files[chrom_key]

    #################################################################
    # sort chromosome-specific files
    #################################################################
    for chrom_key in chrom_files:
        chrom,strand = chrom_key
        chrom_sbed = '%s_%s_%s_sort.bed' % (options.out_prefix,chrom,strand)
        sort_cmd = 'sortBed -i %s > %s' % (chrom_files[chrom_key], chrom_sbed)
        subprocess.call(sort_cmd, shell=True)
        os.remove(chrom_files[chrom_key])
        chrom_files[chrom_key] = chrom_sbed


    #################################################################
    # parse chromosome-specific files
    #################################################################
    final_bed_out = open('%s.bed' % options.out_prefix, 'w')

    for chrom_key in chrom_files:
        chrom, strand = chrom_key

        open_peaks = []
        for line in open(chrom_files[chrom_key]):
            a = line.split('\t')
            a[-1] = a[-1].rstrip()

            # construct Peak
            peak_start = int(a[1])
            peak_end = int(a[2])
            peak_act = activity_set(a[6])
            peak = Peak(peak_start, peak_end, peak_act)

            if len(open_peaks) == 0:
                # initialize open peak
                open_end = peak_end
                open_peaks = [peak]

            else:
                # operate on exiting open peak

                # if beyond existing open peak
                if open_end + options.merge_dist <= peak_start:
                    # close open peak
                    mpeak = merge_peaks(open_peaks, options.peak_size, chrom_lengths.get(chrom,None))

                    # print to file
                    print >> final_bed_out, mpeak.bed_str(chrom, strand)

                    # initialize open peak
                    open_end = peak_end
                    open_peaks = [peak]

                else:
                    # extend open peak
                    open_peaks.append(peak)
                    open_end = max(open_end, peak_end)

        if len(open_peaks) > 0:
            # close open peak
            mpeak = merge_peaks(open_peaks, options.peak_size, chrom_lengths.get(chrom,None))

            # print to file
            print >> final_bed_out, mpeak.bed_str(chrom, strand)

    final_bed_out.close()

    # clean
    for chrom_key in chrom_files:
        os.remove(chrom_files[chrom_key])


    #################################################################
    # construct/update activity table
    #################################################################
    final_act_out = open('%s_act.txt' % options.out_prefix, 'w')

    # print header
    cols = [''] + db_targets
    print >> final_act_out, '\t'.join(cols)

    # print sequences
    for line in open('%s.bed' % options.out_prefix):
        a = line.rstrip().split('\t')
        # index peak
        peak_id = '%s:%s-%s(%s)' % (a[0], a[1], a[2], a[5])

        # construct full activity vector
        peak_act = [0]*len(db_targets)
        for ai in a[6].split(','):
            if ai != '.':
                peak_act[int(ai)] = 1

        # print line
        cols = [peak_id] + peak_act
        print >> final_act_out, '\t'.join([str(c) for c in cols])

    final_act_out.close()


def activity_set(act_cs):
    ''' Return a set of ints from a comma-separated list of int strings.

    Attributes:
        act_cs (str) : comma-separated list of int strings

    Returns:
        set (int) : int's in the original string
    '''
    ai_strs = [ai for ai in act_cs.split(',')]

    if ai_strs[-1] == '':
        ai_strs = ai_strs[:-1]

    if ai_strs[0] == '.':
        aset = set()
    else:
        aset = set([int(ai) for ai in ai_strs])

    return aset


def merge_peaks(peaks, peak_size, chrom_len):
    ''' Merge and grow the Peaks in the given list.

    Attributes:
        peaks (list[Peak]) : list of Peaks
        peak_size (int) : desired peak extension size
        chrom_len (int) : chromsome length

    Returns:
        Peak representing the merger
    '''
    # determine peak midpoints
    peak_mids = []
    peak_weights = []
    for p in peaks:
        mid = (p.start + p.end - 1) / 2.0
        peak_mids.append(mid)
        peak_weights.append(1+len(p.act))

    # take the mean
    merge_mid = int(0.5+np.average(peak_mids, weights=peak_weights))

    # extend to the full size
    merge_start = max(0, merge_mid - peak_size/2)
    merge_end = merge_start + peak_size
    if chrom_len and merge_end > chrom_len:
        merge_end = chrom_len
        merge_start = merge_end - peak_size

    # merge activities
    merge_act = set()
    for p in peaks:
        merge_act |= p.act

    return Peak(merge_start, merge_end, merge_act)


def merge_peaks_greedy(peaks, peak_size, chrom_len):
    '''
    Another approach to this problem would be to use an
    extension size and a maximum overlap.

    Then in the primary loop above, we extend every peak
    immediately and cluster overlaps.

    When there are no more overlaps, we parse that region
    by greedily traversing the list for the shortest
    distance and merging the two.

    This is O(N^2), so I might need a better solution for
    bigger clusters. Like do an initial pass and immediately
    merge any two peaks closer than X bp.
    '''
    pass


class Peak:
    ''' Peak representation

    Attributes:
        start (int)   : peak start
        end   (int)   : peak end
        act   (set[int]) : set of target indexes where this peak is active.
    '''
    def __init__(self, start, end, act):
        self.start = start
        self.end = end
        self.act = act

    def bed_str(self, chrom, strand):
        ''' Return a BED-style line '''
        if len(self.act) == 0:
            act_str = '.'
        else:
            act_str = ','.join([str(ai) for ai in sorted(list(self.act))])
        cols = (chrom, str(self.start), str(self.end), '.', '1', strand, act_str)
        return '\t'.join(cols)

################################################################################
# __main__
################################################################################
if __name__ == '__main__':
    main()
