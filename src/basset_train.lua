#!/usr/bin/env th

require 'hdf5'

require 'batcher'

----------------------------------------------------------------
-- parse arguments
----------------------------------------------------------------
cmd = torch.CmdLine()
cmd:text()
cmd:text('DNA ConvNet training')
cmd:text()
cmd:text('Arguments')
cmd:argument('data_file')
cmd:text()
cmd:text('Options:')
cmd:option('-cuda', false, 'Run on GPGPU')
cmd:option('-job', '', 'Table of job hyper-parameters')
cmd:option('-restart', '', 'Restart an interrupted training')
cmd:option('-result', '', 'Write the loss to this file (useful for Bayes Opt)')
cmd:option('-save', 'dnacnn', 'Prefix for saved models')
cmd:option('-seed', '', 'Seed the model with the parameters of another')
cmd:option('-rand', 1, 'Random number generator seed')
cmd:option('-stagnant_t', 10, 'Allowed epochs with stagnant validation')
cmd:text()
opt = cmd:parse(arg)

-- fix seed
torch.manualSeed(opt.rand)

-- set cpu/gpu
cuda = opt.cuda
require 'convnet'

----------------------------------------------------------------
-- load data
----------------------------------------------------------------
local data_open = hdf5.open(opt.data_file, 'r')
local train_targets = data_open:read('train_out')
local train_seqs = data_open:read('train_in')
local valid_targets = data_open:read('valid_out')
local valid_seqs = data_open:read('valid_in')

local num_seqs = train_seqs:dataspaceSize()[1]
local init_depth = train_seqs:dataspaceSize()[2]
local seq_len = train_seqs:dataspaceSize()[4]
local num_targets = train_targets:dataspaceSize()[2]

----------------------------------------------------------------
-- construct model
----------------------------------------------------------------
local job = {}

-- get parameters
local scientist = nil
if opt.job == '' then
    print("Hyper-parameters unspecified. Applying a small model architecture")
    job.conv_filters = {100,75,100}
    job.conv_filter_sizes = {19,11,7}
    job.pool_width = {3,4,4}

    job.hidden_units = {500,500}
    job.hidden_dropouts = {0.5,0.5}
else
    local job_in = io.open(opt.job, 'r')
    local line = job_in:read()
    while line ~= nil do
        for k, v in string.gmatch(line, "([%w%p]+)%s+([%w%p]+)") do
            -- if key already exsits
            if job[k] then
                -- change to a table
                if type(job[k]) ~= 'table' then
                    job[k] = {job[k]}
                end

                -- write new value to the end
                local jobk_len = #job[k]
                job[k][jobk_len+1] = tonumber(v)
            else
                -- just save the value
                job[k] = tonumber(v)
            end
        end
        line = job_in:read()
    end
    job_in:close()

    print(job)
end

-- initialize
local convnet = ConvNet:__init()

local build_success = true
if opt.restart ~= '' then
    local convnet_params = torch.load(opt.restart)
    convnet:load(convnet_params)
elseif opt.seed ~= '' then
    local convnet_params = torch.load(opt.seed)
    convnet:load(convnet_params)
    convnet:adjust_final(num_targets)
else
    build_success = convnet:build(job, init_depth, seq_len, num_targets)

    if build_success == false then
        print('Invalid model')

        -- update spearmint
        if opt.result ~= '' then
            -- print result to file
            local result_out = io.open(opt.result, 'w')
            result_out:write('1000000\n')
            result_out:close()
        end

        os.exit()
    end
end

convnet.model:training()

----------------------------------------------------------------
-- run
----------------------------------------------------------------
local epoch = 1
local epoch_best = 1
local valid_best = math.huge
local batcher = Batcher:__init(train_seqs, train_targets, batch_size)

while epoch - epoch_best <= opt.stagnant_t do
    io.write(string.format("Epoch #%3d   ", epoch))
    local start_time = sys.clock()

    -- conduct one training epoch
    local train_loss = convnet:train_epoch(batcher)
    io.write(string.format("train loss = %7.3f, ", train_loss))

    -- change to evaluate mode
    convnet.model:evaluate()

    -- measure accuracy on a test set
    local valid_loss, valid_aucs = convnet:test(valid_seqs, valid_targets)
    local valid_auc_avg = torch.mean(valid_aucs)

    -- print w/ time
    local epoch_time = sys.clock()-start_time
    if epoch_time < 600 then
        time_str = string.format('%3ds', epoch_time)
    else
        time_str = string.format('%3dm', epoch_time/60)
    end
    io.write(string.format("valid loss = %7.3f, AUC = %.4f, time = %s", valid_loss, valid_auc_avg, time_str))

    -- save checkpoint
    convnet:sanitize()
    torch.save(string.format('%s_check.th' % opt.save), convnet)

    -- update best
    if valid_loss < valid_best then
        io.write(" best!")
        valid_best = valid_loss
        epoch_best = epoch

        -- save best
        torch.save(string.format('%s_best.th' % opt.save), convnet)
    end

    -- change back to training mode
    convnet.model:training()

    -- increment epoch
    epoch = epoch + 1

    print('')
end

if opt.result ~= '' then
    -- print result to file
    local result_out = io.open(opt.result, 'w')
    result_out:write(valid_best, '\n')
    result_out:close()
end

data_open:close()